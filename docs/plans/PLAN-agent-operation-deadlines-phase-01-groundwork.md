# Agent operation deadlines phase 1: attribute field mask and command dispatch registry

## Prompt

This phase lays the two pieces of groundwork every later phase of
`PLAN-agent-operation-deadlines.md` writes against, and changes no
externally visible behaviour. The first is a field mask on
`update_agent_operation_attributes`, without which phase 2's
`last_progress` and `attempts` attributes would be silently clobbered
by `add_result()`. The second is a per-command dispatch registry on
`SideChannelExecutorJob`, so that phase 4's progress hooks and phase
5's retry bound have one place to ask "what can this command do?"
instead of a widening if/elif chain.

Ground every change in the tree: `shakenfist/mariadb.py` (the
three-layer direct/gRPC/public pattern), `protos/database.proto`,
`shakenfist/daemons/database/main.py` (the gRPC servicer),
`shakenfist/tests/mock_mariadb.py`, `shakenfist/operations/
agentoperation.py` (the only caller), and
`shakenfist/daemons/sidechannel/main.py` (the executor).

**Planning effort:** medium. Both changes follow patterns that
already exist in the tree for other object types; the research is
recorded below so the implementing agents do not repeat it. The one
part that is not mechanical -- editing the executor's protocol loop,
which is the exact code path behind the #3516 flake -- is called out
with its own back brief gate.

## Scope

In:

- A `fields` mask on `update_agent_operation_attributes` through all
  three layers, the proto, the gRPC servicer, the test mock and the
  single caller, with unit tests.
- A handler class per agent command, replacing the
  `cmd['command'] == ...` if/elif chain, each declaring its
  `reports_progress` and `retryable` capabilities and owning its
  dispatch body.
- Initialising the get-file transfer state in `__init__` so its
  already-written "unknown file transfer" guard can actually fire.

Out:

- Every schema change. The `agent_operation_attributes` table is not
  touched; the mask names only the `results` column that exists
  today. `deadline`, `progress_timeout`, `last_progress` and
  `attempts` all arrive in phase 2.
- Consuming the new capability flags. Phase 1 declares
  `reports_progress` and `retryable`; phase 4 and phase 5 read them.
- Any change to the reply-handling if/elif chain, the `ready` /
  `outstanding_message_count` bookkeeping, or the `execute()` finally
  block from PR #3506.
- The unguarded `self.agentop.state = STATE_ERROR` writes the phase 0
  audit catalogued. They are only a problem once `expired` exists, so
  they belong to phase 4.

## What the survey found

The survey checked the master plan's phase 1 claims against the tree.
Both premises hold, one description is wrong, and one latent defect
turned up in the code the registry refactor touches.

1. **The field-mask premise holds, and the pattern to copy is
   exact.** `_direct_update_agent_operation_attributes`
   (`mariadb.py:18531`) writes `results=...` unconditionally with no
   mask parameter; `_grpc_update_agent_operation_attributes`
   (`mariadb.py:18683`) and the public wrapper (`mariadb.py:18807`)
   likewise. The network equivalents are the model to follow:
   `_network_attributes_column_values` (`mariadb.py:17721`) builds an
   `all_values` dict and raises `ValueError` on an unknown field name;
   `_direct_update_network_attributes` (`mariadb.py:17745`) and
   `_grpc_update_network_attributes` (`mariadb.py:18014`) take
   `fields`; and the public `update_network_attributes`
   (`mariadb.py:18207`) takes `fields` as a **required positional**
   and calls the column-values helper once before dispatch purely to
   validate the mask, so a bad field name raises on the gRPC path too
   rather than becoming a discarded `StatusReply` failure.
2. **There is exactly one caller.** `AgentOperation.add_result()`
   (`operations/agentoperation.py:183`) is the only call site outside
   `mariadb.py` itself, and it already holds
   `get_lock_attr('results')` around its read-modify-write.
3. **The proto has no mask field.**
   `UpdateAgentOperationAttributesRequest` (`protos/database.proto:
   2114`) carries only `data = 1`, where the network and instance
   equivalents (`database.proto:1759` and `:2222`) carry
   `repeated string fields = 2;` with an explanatory comment. The
   servicer's `UpdateAgentOperationAttributes`
   (`daemons/database/main.py:4045`) correspondingly calls the direct
   function with no `fields`, where `UpdateNetworkAttributes`
   (`daemons/database/main.py:3260`) passes
   `fields=list(request.fields)`.
4. **The metrics counter already exists.**
   `update_agent_operation_attributes` is registered in the Monitor
   operations list (`daemons/database/main.py:6000`), so no counter
   work is needed.
5. **There is a canonical home for the tests.**
   `shakenfist/tests/test_mariadb_instance_attributes.py` holds one
   `*ColumnValuesTestCase` per masked object type (network's is at
   line 110), each asserting no-mask-returns-every-column,
   mask-limits-columns and unknown-field-rejected, plus
   `mock_mariadb.py:2333` shows how the mock applies a mask.
6. **The dispatch chain is where the master plan says it is**, at
   `daemons/sidechannel/main.py:830-849`, covering `execute`,
   `put-blob`, `chmod` and `get-file` -- and those four are the
   complete set, constructed only in `external_api/instance.py` at
   lines 1623, 1628, 1673 and 1716. (Those four line numbers were
   originally recorded as 1663/1668/1713/1756, read from a worktree
   sitting on the pre-merge plans branch, where that file predates
   the API-validation work already on `develop`. The command set was
   unaffected; only the line numbers were wrong. Corrected during
   step 1b. Every other line reference in this plan was checked
   against the merged tree by the implementing steps and found
   accurate to within one line.)
7. **Correction: the registry cannot own reply handlers.** The
   master plan's "Command dispatch restructure" section says each
   registry entry declares "its dispatch method and reply
   handler(s)". It cannot. Replies are dispatched by protobuf field
   (`main.py:741-798`), not by command name, and the keys do not map
   one-to-one: `file_chunk_reply` is the agent's ack for put-blob
   chunks while `file_chunk` carries get-file payloads, and the same
   handler serves whichever command is in flight. The registry
   therefore owns dispatch and capabilities only; phase 4's
   `observe_progress()` calls go inside the existing reply handlers.
   **This has been corrected in the master plan at source as part of
   this planning commit** -- a later step need not redo it.
8. **Latent defect: the get-file transfer state is never
   initialised.** `_dispatch_get_file` (`main.py:545-549`) is the only
   place `_agent_path_for_get`, `_blob_uuid`, `_blob_partial_file` and
   `_stat_result` are assigned; `__init__` (`main.py:307-324`)
   initialises `chunk_iterator` but not these. So the guards at
   `main.py:567` and `main.py:589` that intend to raise
   `GetException('Unknown file transfer')` instead raise
   `AttributeError` if a `stat_result` or `file_chunk` ever arrives
   before a get-file dispatch. `_handle_stat_result` is called
   unguarded at `main.py:787` (only `_handle_file_chunk` is wrapped in
   `except GetException`), so that `AttributeError` escapes the loop
   entirely. Since PR #3506 it is no longer fatal to the operation --
   `execute()`'s finally marks it errored -- but the intended error
   never surfaces.

Two documentation drifts were also found and are fixed in this
planning commit: the master plan's phase table now links this file,
and the `docs/plans/index.md` master-plan row still said "Not
started" after phase 0 completed.

## Decisions

1. **The registry is one handler class per command, instantiated
   once per executor job.** Chosen by the operator at the phase 1
   back brief, over a flat table of descriptors holding unbound
   methods. Shape:

   ```python
   class AgentCommandHandler:
       """Dispatch and capabilities for one agent command verb."""

       name: str = ''
       reports_progress = False
       retryable = True
       register_as_outstanding = False

       def __init__(self, job):
           self.job = job

       def dispatch(self, command_id, cmd):
           raise NotImplementedError


   class PutBlobCommand(AgentCommandHandler):
       name = 'put-blob'
       reports_progress = True
       register_as_outstanding = True

       def dispatch(self, command_id, cmd):
           ...  # body moved from _dispatch_put_blob

   AGENT_COMMAND_HANDLERS = [
       ExecuteCommand, PutBlobCommand, ChmodCommand, GetFileCommand]
   ```

   The handler classes take the job at construction, so they are
   defined *before* `SideChannelExecutorJob` and its `__init__`
   builds `self.command_handlers = {cls.name: cls(self) for cls in
   AGENT_COMMAND_HANDLERS}`. The dispatch block becomes a lookup in
   that dict. Rationale for the extensibility this buys: phases 4 and
   5, and later the dependency plan, all add per-command behaviour,
   and a subclass is the natural place for it.

   **Instantiated once per job, not once per dispatch.** The
   operator's sketch built a handler at each dispatch. A per-dispatch
   handler cannot hold state across the dispatch/reply boundary --
   and that boundary is where phase 4's `observe_progress()` lives,
   and where `chunk_iterator` and the get-file transfer state already
   live today. A per-job handler leaves phase 4 free to migrate that
   state onto the handler that owns it. In phase 1 the state stays on
   the job, because the reply handlers that read it are job methods
   and moving them is out of scope.
2. **`reports_progress` and `retryable` are both declared now,
   unconsumed.** Phase 0 decided `execute` is not retried via a
   per-command `retryable` flag; adding it in phase 5 instead would
   mean editing every handler a second time. All the capability
   answers should be readable from one class. Values: `ExecuteCommand`
   (progress False, retry False -- an in-flight `execute` cannot be
   cancelled agent-side, so a retry risks a second copy of a
   side-effecting command), `PutBlobCommand` (True, True),
   `GetFileCommand` (True, True), `ChmodCommand` (False, True).
3. **`add_result()` passes `fields=['results']` and keeps its lock.**
   The mask and the lock solve different problems: the mask stops a
   concurrent writer of a *different* attribute losing its column to
   this writer's stale snapshot, and the lock stops two writers of the
   *same* results dict losing an entry to each other's merge. Phase 2
   adds `last_progress` and `attempts`, at which point dropping either
   one would be a bug.
4. **`fields` is a required positional on the public wrapper**,
   matching `update_network_attributes`. A defaulted parameter lets a
   future caller forget the mask and reintroduce exactly the failure
   this phase exists to prevent; making it positional means the
   compiler asks the question.
5. **The get-file state initialisation is in scope, as its own
   commit** (confirmed by the operator at the phase 1 back brief,
   over dropping it in favour of an issue). It is four lines in the
   `__init__` the registry work
   already brings into focus, phase 4 will add per-command progress
   state to that same `__init__`, and it converts an `AttributeError`
   that escapes the protocol loop into the `GetException` the author
   already wrote. This is the decision most likely to be argued with,
   because this phase otherwise promises no behaviour change: the
   counter-argument is that it is a bug fix and belongs in its own
   issue-fix branch. The case for doing it here is that it is
   strictly smaller than the issue-filing ceremony around it, it is
   confined to the class being refactored, and leaving a known
   `AttributeError` in the path phases 4 and 5 are about to build a
   reaper on invites a confusing failure later.
6. **No schema change, and therefore no migration.** The mask names
   only the `results` column, which exists. `sf-ctl
   ensure-mariadb-schema` is unaffected by this phase.
7. **Rolling upgrades are safe in both directions and need no
   sequencing note.** A new client sending `fields` to an old
   `sf-database` has the unknown proto3 field ignored, so that server
   writes every column -- which is precisely today's behaviour, and
   today only one column exists. An old client reaching a new
   `sf-database` sends no `fields`, which the servicer turns into an
   empty list, which means "write every column". Neither direction can
   regress before phase 2 adds a second column.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | Add a `fields` mask to the agent operation attributes update, mirroring the network attributes equivalents exactly. In `shakenfist/mariadb.py`: add `_agent_operation_attributes_column_values(data, fields=None)` next to the other agent operation helpers, modelled on `_network_attributes_column_values` at line 17721 (build an `all_values` dict -- for this type just `{'results': _json_dumps(data.results)}` -- return it whole when `fields` is falsy, otherwise raise `ValueError` naming any unknown fields and return the subset); give `_direct_update_agent_operation_attributes` (line 18531) and `_grpc_update_agent_operation_attributes` (line 18683) an `Optional[List[str]] = None` `fields` parameter and use the helper / pass `fields=fields or []` into the request, copying lines 17745 and 18014; change the public `update_agent_operation_attributes` (line 18807) to take `fields` as a **required positional** and call the helper once before dispatch to validate the mask, copying line 18207 including its comment about why validation happens before dispatch. In `protos/database.proto`, add `repeated string fields = 2;` to `UpdateAgentOperationAttributesRequest` (line 2114) with a comment in the style of the one on `UpdateNetworkAttributesRequest` (line 1759), then regenerate with `tox -e genprotos` -- **never** run `grpc_tools.protoc` directly -- and commit the regenerated stubs in the same commit. In `shakenfist/daemons/database/main.py`, make `UpdateAgentOperationAttributes` (line 4045) pass `fields=list(request.fields)`, copying `UpdateNetworkAttributes` at line 3260; the metrics counter is already registered at line 6000, do not add one. In `shakenfist/tests/mock_mariadb.py`, give `_mariadb_update_agent_operation_attributes` (line 2472) the same signature and mask-application logic as `_mariadb_update_network_attributes` (line 2333). In `shakenfist/operations/agentoperation.py`, make `add_result()`'s call at line 183 pass `fields=['results']`; leave the surrounding `get_lock_attr('results')` alone, it guards a different race. Add an `AgentOperationColumnValuesTestCase` to `shakenfist/tests/test_mariadb_instance_attributes.py` following the `NetworkColumnValuesTestCase` at line 110 (no-mask, mask-limits, unknown-field-rejected). Do not touch the `agent_operation_attributes` table definition -- this phase adds no columns. |
| 1b | high | opus | worktree | Replace the command dispatch if/elif chain in `shakenfist/daemons/sidechannel/main.py` with one handler class per command, with no behaviour change. Today lines 830-849 inside `_execute_inner` branch on `cmd['command']` across `execute`, `put-blob`, `chmod` and `get-file`, with an else that sets the operation errored with 'unknown command', and `put-blob` additionally sets `register_as_outstanding = True`. Define, *above* `SideChannelExecutorJob` (the handlers take the job at construction, so they do not need it to exist first): an `AgentCommandHandler` base with class attributes `name = ''`, `reports_progress = False`, `retryable = True`, `register_as_outstanding = False`, an `__init__(self, job)` storing `self.job`, and a `dispatch(self, command_id, cmd)` raising `NotImplementedError`; then `ExecuteCommand` (progress False, retryable False), `PutBlobCommand` (progress True, retryable True, register_as_outstanding True), `ChmodCommand` (progress False, retryable True) and `GetFileCommand` (progress True, retryable True); then a module-level `AGENT_COMMAND_HANDLERS` list of the four classes. Move the bodies of `_dispatch_execute` (line 365), `_dispatch_put_blob` (line 468), `_dispatch_chmod` (line 495) and `_dispatch_get_file` (line 536) into the corresponding `dispatch()` methods and delete the originals. **The moved bodies must be character-identical except that every `self.` becomes `self.job.`** -- no reflowing, no renaming, no tidying, so the diff is reviewable as a pure move. In `SideChannelExecutorJob.__init__`, build `self.command_handlers = {cls.name: cls(self) for cls in AGENT_COMMAND_HANDLERS}` -- once per job, not once per dispatch, because a per-dispatch handler could not hold state across the dispatch/reply boundary that phase 4 needs. The dispatch block becomes a lookup in `self.command_handlers`, the unknown-command else-branch preserved verbatim on a miss, then `requests = handler.dispatch(command_id, cmd)` and `register_as_outstanding = handler.register_as_outstanding`. Constraints, all of them load-bearing: do **not** touch the reply-handling if/elif chain at lines 741-798 (it is keyed on protobuf field, not command name, and does not map one-to-one onto commands) or the reply handler methods it calls, which stay on the job and keep reading job state; do **not** move `chunk_iterator` or the get-file transfer state onto the handlers, that is phase 4's call; do **not** touch the `ready` / `outstanding_message_count` bookkeeping, the `finally` block at lines 868-870, the welcome and execution-timeout checks at lines 694-713, or `AGENT_OPERATION_EXECUTION_TIMEOUT` (phase 4 removes it); do **not** consume `reports_progress` or `retryable` anywhere -- they are declared now and read in phases 4 and 5; leave `_dispatch_chmod`'s bare `return` on an undecodable mode as it is, since a falsy return already means 'nothing to send' and changing it to an error would be a behaviour change. Note that `_dispatch_put_blob` sets the operation errored and returns `[]` on a missing blob (lines 470 and 476) -- that stays as-is (as `self.job.agentop.state = ...`), phase 4 revisits those writes once `expired` exists. Add a unit test to `shakenfist/tests/test_daemon_sidechannel_executor.py` (which builds executors with `__new__`, see `_make_executor` at line 26) asserting that the `name` values across `AGENT_COMMAND_HANDLERS` are exactly the four command names constructed in `shakenfist/external_api/instance.py` at lines 1623, 1628, 1673 and 1716, that no two handlers share a name, and that each handler overrides `dispatch`, and that the declared `(reports_progress, retryable, register_as_outstanding)` values match the table above -- the capability assertion is what keeps the flags honest while nothing in production reads them, and without it the "declared but unconsumed" done-criterion below is unsatisfiable. Back-brief the concrete class layout before editing (see Back brief). |
| 1c | low | sonnet | none | Initialise the get-file transfer state in `SideChannelExecutorJob.__init__` in `shakenfist/daemons/sidechannel/main.py`. `__init__` (lines 307-324) sets `self.chunk_iterator = None` but never sets `_agent_path_for_get`, `_blob_uuid`, `_blob_partial_file` or `_stat_result`; they are first assigned in `_dispatch_get_file` at lines 545-549. As a result the `if not self._blob_partial_file` guards at lines 567 and 589, which exist to raise `GetException('Unknown file transfer')`, raise `AttributeError` instead when a `stat_result` or `file_chunk` arrives with no get-file in flight -- and `_handle_stat_result` is invoked unguarded at line 787, so it escapes the protocol loop rather than being handled. Set all four to `None` in `__init__` alongside `chunk_iterator`. Add a unit test to `shakenfist/tests/test_daemon_sidechannel_executor.py` that builds an executor the way `_make_executor` (line 26) does, calls `_handle_stat_result` with a stubbed reply and asserts `GetException` is raised. Change nothing else -- in particular do not add a `try/except GetException` around line 787; that is a real behaviour question for phase 4. |

Each step is its own commit. 1a and 1b are independent and may be
done in either order; 1c follows 1b so the two touch `__init__`
once each rather than conflicting.

## Risks and mitigations

- **1b edits the exact loop behind the #3516 flake.** A subtle
  reordering there would be invisible to unit tests and would surface
  as a merge-queue flake weeks later. Mitigation: the brief fences off
  everything except lines 830-849 and the four `_dispatch_*` bodies;
  the management session reads the diff and confirms the only semantic
  change is lookup-versus-branch (specifically that
  `register_as_outstanding` is still set for put-blob and only
  put-blob, and that a dispatch returning falsy still skips the send
  while leaving `ready` True); and the branch does not merge until
  `test_agentops` passes in cluster CI, which is the functional
  exercise of all four commands.
- **Moving four method bodies is a wider diff than a lookup table
  would have been**, and the handler shape means every `self.` in
  them becomes `self.job.`, which is exactly the kind of sweep in
  which one missed or over-eager substitution hides. This is the cost
  of the chosen shape and is accepted deliberately. Mitigations: the
  brief requires the moved bodies to be otherwise character-identical
  so a reviewer can read the diff as a pure move; mypy runs in
  pre-commit and will catch a `self.job.` that should have stayed
  `self.` inside a handler; and the step is done at high effort on
  opus rather than the medium/sonnet a table would have justified.
- **1b is done in a worktree and the class layout is disliked at
  review.** Mitigation: the back brief gate below settles the layout
  before any editing, and worktree isolation makes discarding cheap.
- **Regenerated proto stubs drift from the pinned library
  versions.** Mitigation: `tox -e genprotos` is mandated in the
  brief; it pins the `pyproject.toml` versions and does the import
  rewriting that raw `protoc` does not. The management session checks
  that the regenerated `shakenfist/protos/` files are in the same
  commit as the `.proto` edit.
- **The required-positional `fields` breaks an out-of-tree caller.**
  Mitigation: the survey found exactly one caller in this repository,
  and `mariadb.py` is not part of any published client API -- the
  sibling `client-python` talks REST, not gRPC. `pre-commit` runs
  mypy, which will find any missed call site.
- **1c's behaviour change is judged out of scope at review.**
  Mitigation: it is a separate commit and can be dropped from the PR
  without touching 1a or 1b.

## Definition of done

Each of these is checkable, and the first five are a script:

```bash
# 1. The public wrapper takes a mask, and the sole caller names it.
grep -A2 '^def update_agent_operation_attributes' shakenfist/mariadb.py | grep -q 'fields'
grep -q "fields=\['results'\]" shakenfist/operations/agentoperation.py

# 2. The proto carries the mask and the stubs were regenerated with it.
#    The -A window must clear the explanatory comment above the field
#    (four lines here, where the network message this copies has three).
grep -A10 'message UpdateAgentOperationAttributesRequest' protos/database.proto | grep -q 'repeated string fields = 2;'
git diff --name-only HEAD~1 | grep -q 'shakenfist/protos/database_pb2'

# 3. No command-name branching, and no command dispatch methods,
#    survive on the job -- every dispatch body now lives on a handler
#    class. Note the pattern must name the four command methods:
#    Monitor._dispatch_loop is the dispatcher *thread* loop and is
#    deliberately untouched, so a bare 'def _dispatch_' matches it and
#    fails on correct work.
test 0 -eq "$(grep -c "cmd\['command'\] ==" shakenfist/daemons/sidechannel/main.py)"
test 0 -eq "$(grep -cE 'def _dispatch_(execute|put_blob|chmod|get_file)' shakenfist/daemons/sidechannel/main.py)"

# 4. The capability flags are declared and not yet consumed: the only
#    files mentioning them are the daemon module that defines the
#    table and the test that asserts its shape.
test 2 -eq "$(grep -rl 'reports_progress' shakenfist/ --include=*.py | wc -l)"

# 5. The get-file state is initialised where it is declared.
grep -A20 'class SideChannelExecutorJob' shakenfist/daemons/sidechannel/main.py | grep -q '_blob_partial_file = None'
```

And, by inspection:

- `_agent_operation_attributes_column_values` raises `ValueError` for
  an unknown field name, asserted by a test in
  `test_mariadb_instance_attributes.py`.
- The `name` values across `AGENT_COMMAND_HANDLERS` equal the four
  command names built in `external_api/instance.py`, are unique, and
  every handler overrides `dispatch` -- asserted by a test rather
  than by reading.
- Calling `_handle_stat_result` on a freshly constructed executor
  raises `GetException`, not `AttributeError`, asserted by a test.
- No fact about the registry is stated differently in the master
  plan's "Command dispatch restructure" section and in the code:
  specifically, neither claims the registry owns reply handlers.
- `pre-commit run --all-files` passes (flake8, stestr, mypy).
- Cluster CI's `test_agentops` passes on the branch.

## Back brief

Before executing any step, back brief the operator on the
understanding of this phase and how the intended work aligns with it.

**Gate on step 1b.** The registry's *shape* was settled by the
operator at the phase 1 back brief (handler classes, per-job
instantiation -- decision 1). What still needs agreement before any
edit to `daemons/sidechannel/main.py` is the concrete layout: the
`AgentCommandHandler` attribute list, where the classes sit relative
to `SideChannelJob` and `SideChannelExecutorJob`, and the exact
rewritten form of the dispatch block at lines 830-849. Proposing
that is cheap; discovering at review that the layout is wrong means
redoing a four-body move in the most flake-prone loop in the daemon.
