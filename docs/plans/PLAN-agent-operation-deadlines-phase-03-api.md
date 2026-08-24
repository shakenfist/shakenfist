# Agent operation deadlines phase 3: the API surface

## Prompt

Plan the next phase of `PLAN-agent-operation-deadlines.md` with the
`next-phase` skill, after phase 2 merged as PR #3858. Phase 2 added the
columns; this phase lets a client set them.

## Planning effort

High. The phase is small in lines of code and large in decisions: three
of the four questions it answers (what an omitted parameter writes,
whether an out-of-range value is coerced or refused, and whether
`execute` exposes a progress timeout it can never honour) determine
what phase 4 is allowed to assume, and getting any of them wrong is
expensive to unwind once rows carry the values.

## Scope

**In scope.**

- `deadline_seconds` on all three creating endpoints, and
  `progress_timeout_seconds` on `agent/put` and `agent/get` — see
  decision 4 for why `execute` does not get the second one.
- Their declarations, their `STRUCTURED_PARAMETERS` entries, and the
  handler guard that backs the published bound.
- Two config options, `AGENT_OPERATION_DEFAULT_DEADLINE` (600) and
  `AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT` (30), read *only* by the
  API server when it converts a request into stored values.
- The request-side API reference documentation.

**Out of scope.**

- All enforcement. Nothing dequeues, expires, times out or reaps in
  this phase; a row simply carries values nobody reads. Phase 4 owns
  `AGENT_OPERATION_EXECUTION_TIMEOUT`
  (`shakenfist/daemons/sidechannel/main.py:56`, a module constant, not
  a config option), the `expired` state, and the three enforcement
  points.
- `client-python`. Phase 6.
- Flipping `API_VALIDATION_MODE` to `enforce`. That belongs to
  `PLAN-api-input-validation.md`, and this phase must not depend on it
  — see decision 3.

## What the survey found

The master plan's *API surface* section (lines 186-203) is factually
correct against the tree. Every claim checks out:

- The three creating endpoints exist as
  `InstanceAgentPutEndpoint` (`shakenfist/external_api/instance.py:1648`),
  `InstanceAgentGetEndpoint` (`:1716`) and
  `InstanceAgentExecuteEndpoint` (`:1755`), routed at
  `shakenfist/external_api/app.py:455-460`.
- The `number` type token exists
  (`shakenfist/external_api/base.py:273`), and the optional sixth
  constraints element accepts `minimum`
  (`CONSTRAINT_KEYS`, `base.py:230`).
- `STRUCTURED_PARAMETERS` exists, at
  `shakenfist/tests/external_api/test_openapi_spec.py:104` — note the
  path is under `tests/external_api/`, not `tests/`.
- `AgentOperation.new()` already accepts `deadline` and
  `progress_timeout` keyword arguments
  (`shakenfist/operations/agentoperation.py:115-127`), so no object
  change is needed here at all. Phase 2 shipped that ahead of its
  consumer deliberately.
- Neither config option exists yet, as the plan says.

Four things the plan does not say, which change what this phase does:

1. **A deadline-aware API server writes a value on every create.**
   Buried in the enforcement section (line 245): "a deadline-aware API
   server always writes an absolute timestamp or the explicit `0.0`
   sentinel". So an omitted `deadline_seconds` does **not** store NULL
   and leave phase 4 to apply the default. It stores
   `time.time() + AGENT_OPERATION_DEFAULT_DEADLINE`. This is the
   correct reading and it matters: the deadline is defined as "since
   this REST request was received", and only the API node knows when
   that was. NULL is reserved for rows written by a pre-upgrade API
   node, where phase 4 falls back to anchoring at dispatch time. Read
   quickly, the *API surface* section's "Omitted means the server
   default (600)" is easy to mistake for "store NULL, resolve later";
   the phase 2 schema comments make the same sentence sound like the
   database's problem. It is not.

2. **The published bound is backed by coercion, not rejection, in the
   one existing precedent.** `('limit', 'body', 'integer', ...,
   {'minimum': 1, 'maximum': 1000})` at
   `shakenfist/external_api/instance.py:1174` is backed by clamping
   inside `_direct_get_object_events`
   (`shakenfist/mariadb.py:5747`: `limit <= 0` becomes 100,
   `limit > 1000` becomes 1000) — the handler itself checks nothing.
   So "publish what the server backs" has an established answer here,
   and it is not a 400. Decision 3 departs from it, with reasons.

3. **`STRUCTURED_PARAMETERS` entries must describe the published shape
   in full.** `test_structured_parameters_publish_their_real_shape`
   (`test_openapi_spec.py:247`) fails on a constraint key that is
   published but not listed, as well as on one listed but not
   published, and `test_every_published_structure_or_bound_is_registered`
   derives the required entries from the specification. So the entries
   are mechanical once the declarations are settled, and they cannot
   silently fall behind.

4. **An undeclared body key is a 400, so this is a one-way
   compatibility change.** `log_request` does `kwargs.update(j)`
   (`base.py:1124`) and the handler signature has no `**kwargs`, so a
   body key no handler accepts raises `TypeError` and answers 400 —
   the comment at `base.py:1108-1113` says so explicitly. A
   phase-6 client sending `deadline_seconds` to a not-yet-upgraded
   API node therefore gets a 400, not a silently ignored parameter.
   That is phase 6's problem to sequence, not this phase's to solve,
   but it belongs in the risk register and in the phase 6 brief.

Nothing in the master plan needed correcting at source except the
`execute` progress-timeout question, which is decision 4 and is
corrected in the *API surface* section as part of the planning commit.
The *Object model and schema* section, rewritten during phase 2
planning, is consistent with all of the above and is left alone.

## Decisions

1. **An omitted parameter stores a computed value, not NULL.**
   `deadline_seconds` omitted stores
   `time.time() + config.AGENT_OPERATION_DEFAULT_DEADLINE`;
   `progress_timeout_seconds` omitted stores
   `config.AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT`. NULL is written
   by nothing in this phase and remains the exclusive signature of a
   row from a pre-upgrade API node. This follows survey finding 1 and
   is what makes the deadline mean what the plan says it means:
   receipt-anchored, not dispatch-anchored.

2. **`0` stays the client's sentinel and is stored as `0.0`.** A
   client passing `deadline_seconds: 0` gets `deadline = 0.0`, which
   phase 2 defined as "the caller asked for none". This is the
   no-deadline-plus-progress-timeout use case the master plan calls
   first class (line 96), so it must survive the API layer intact and
   must not be confused with the falsiness of an omitted parameter.
   Implementation note, because this is the likeliest bug in the
   phase: the parameter default must be `None` and the test must be
   `is None`, never a truthiness test.

3. **An out-of-range or non-numeric value is refused with a 400, not
   coerced.** Negative seconds are refused; a value that will not
   `float()` is refused. This departs from the events-`limit`
   precedent in survey finding 2, deliberately. Clamping a negative
   `deadline_seconds` has no defensible target: 0 means "no deadline",
   which is the *opposite* of what a negative asks for, and the
   default means "ignore what you asked for". Both are silent
   reinterpretations of a timeout, which is the class of behaviour
   this plan exists to remove. The declaration publishes
   `{'minimum': 0}` and the handler is what backs it, so the published
   specification stays true whatever `API_VALIDATION_MODE` is set to
   — this phase must not acquire a dependency on the validation
   compiler reaching `enforce`.

4. **`agent/execute` gains `deadline_seconds` only, and stores
   `progress_timeout = 0.0`.** No command `execute` can build reports
   progress: `ExecuteCommand.reports_progress` is `False`
   (`shakenfist/daemons/sidechannel/main.py:310-311`, from phase 1),
   while `PutBlobCommand` (`:339`) and `GetFileCommand` (`:417`) are
   `True`. Publishing a knob on `execute` that phase 4 can never
   consult would be a parameter that accepts input and does nothing.
   Storing an explicit `0.0` rather than the default keeps the row
   truthful ("this operation has no progress timeout") and preserves
   NULL as meaning only "pre-upgrade API node". **This is the decision
   most likely to be argued with**, and the counter-case is real: it
   costs the API its uniformity across the three sibling endpoints,
   and if `execute` ever grows streaming output it would have to be
   added back as a new parameter. The alternative — accept it
   everywhere and let phase 4 ignore it for `execute` — is one line
   shorter and one lie longer. If a reviewer prefers uniformity, this
   is a cheap decision to reverse before implementation and an
   expensive one after, so it is gated in the back brief.

5. **Conversion lives in one shared helper, not three copies.** A
   module-level helper in `shakenfist/external_api/base.py` returning
   the established `(value, error)` tuple — the pattern already used
   at `base.py:218-222` — takes the two raw parameters plus a
   `progress_capable` flag and returns either
   `((deadline, progress_timeout), None)` or `(None, <400 response>)`.
   Three near-identical six-line blocks in three handlers is how the
   parameters drift apart, and the helper is also the single place a
   test can drive without standing up three requests.

6. **The config options land in this phase even though only the API
   reads them.** Phase 4 will read `AGENT_OPERATION_DEFAULT_DEADLINE`
   for the legacy-NULL fallback path, but the API server needs both
   now, and the parameter documentation has to name the default it
   describes. Precedent within this plan: phase 1 shipped
   `reports_progress` and `retryable` declared-but-unconsumed.

7. **No new endpoint, no change to the response.** `external_view()`
   already returns all four values as of phase 2, so the response
   examples in the API reference are already correct and must not be
   touched again. Only the request side is documented here.

8. **The three endpoints' behaviour is otherwise unchanged.** No
   change to the audit events, the `PREFLIGHT`/`QUEUED` state
   choices, or the `na_create_and_enqueue` call. A diff touching those
   is out of scope and should be rejected in review.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | medium | sonnet | none | Add the two config options to `SFConfig` in `shakenfist/config.py`, in the API Options block alongside `API_ASYNC_WAIT` (line 187) and `API_VALIDATION_MODE` (line 226), following that block's `Field(<default>, description=(...))` shape. `AGENT_OPERATION_DEFAULT_DEADLINE: int = 600` — the wall-clock budget in seconds applied to an agent operation whose creator did not ask for one, converted to an absolute timestamp by the API server at request receipt; the description must say it replaces the hardcoded 900 second `AGENT_OPERATION_EXECUTION_TIMEOUT` in `shakenfist/daemons/sidechannel/main.py` and that phase 4 of the plan is what deletes that constant, because until then both exist. `AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT: int = 30` — seconds without forward progress which are fatal, applied to operations that contain a progress-capable command; the description must record where 30 came from (phase 0 measurement: worst observed CI transfer 625 MB in 2.83 s, 48 of 50 under 0.44 s, so ~10x headroom over the worst total duration while detecting the #3516 wedge 30x faster than 900 s). Do not read either option anywhere in this step. Commit subject: `Add agent operation timing defaults.` |
| 3b | high | opus | none | The conversion helper and its tests, with no endpoint wired to it yet, so the semantics are settled before three call sites exist. In `shakenfist/external_api/base.py`, add a module-level `agent_operation_timing(deadline_seconds, progress_timeout_seconds, progress_capable)` returning `(values, error)` where `values` is a `(deadline, progress_timeout)` tuple — mirror the `(value, error)` convention at `base.py:218-222` and return `(None, sf_api.error(400, ...))` on bad input. Rules, all load-bearing: a parameter is "omitted" **only** when it `is None`, never when it is falsy, because `0` is the client's explicit no-deadline sentinel (decision 2); omitted `deadline_seconds` yields `time.time() + config.AGENT_OPERATION_DEFAULT_DEADLINE`; `deadline_seconds == 0` yields exactly `0.0` and must not be converted to an absolute timestamp; any other value yields `time.time() + float(deadline_seconds)`; omitted `progress_timeout_seconds` yields `float(config.AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT)` when `progress_capable` is true and `0.0` when it is not (decision 4); a supplied `progress_timeout_seconds` is passed through as a float whatever `progress_capable` says, so a caller is never silently overruled. Refuse with 400 anything that will not `float()` (catch `TypeError` and `ValueError` — note a JSON `true` is a Python `bool` and `float(True)` is `1.0`, so reject `bool` explicitly before converting) and anything negative. Booleans are the non-obvious case: `isinstance(True, int)` is true in Python, so a type check that forgets it lets `deadline_seconds: true` through as one second. Add `shakenfist/tests/external_api/test_agent_operation_timing.py` covering: omitted-both for a progress-capable and a non-progress-capable operation; explicit `0` for each parameter separately and together; a fractional value; a negative value for each; a non-numeric string; a `True`; and that a supplied `progress_timeout_seconds` survives `progress_capable=False`. Freeze time with `mock.patch('time.time', return_value=...)` so the absolute-deadline assertions are exact rather than approximate. Commit subject: `Convert agent operation timing requests.` |
| 3c | high | opus | none | Wire the three endpoints in `shakenfist/external_api/instance.py`. `InstanceAgentPutEndpoint.post` (line 1668) and `InstanceAgentGetEndpoint.post` (line 1729) gain `deadline_seconds=None, progress_timeout_seconds=None` kwargs and call the helper with `progress_capable=True`; `InstanceAgentExecuteEndpoint.post` (line 1771) gains `deadline_seconds=None` only and calls it with `progress_capable=False` (decision 4). On an error return the error response immediately, before any event is added and before `AgentOperation.new()` — a refused request must leave no trace beyond the request log. Pass the results through as `AgentOperation.new(..., deadline=deadline, progress_timeout=progress_timeout)`; the signature already accepts both (`shakenfist/operations/agentoperation.py:115`). Add the declarations to each `swagger_helper` list as five-element tuples plus the sixth constraints element: `('deadline_seconds', 'body', 'number', '<description>', False, {'minimum': 0})` and the same shape for `progress_timeout_seconds`. The descriptions must state the sentinel and the default in words, because that text is the published API documentation: for deadline, that the operation must not be dispatched or continue executing more than this many seconds after the request was received, that 0 means no wall-clock deadline, and that omitting it applies `AGENT_OPERATION_DEFAULT_DEADLINE` (600 s by default); for progress timeout, that it is seconds without forward progress, that 0 disables it, and that omitting it applies `AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT` (30 s by default). Do not add a `location` other than `body` — these arrive in the JSON body and `tools/fix-api-parameter-locations.py --apply` will correct drift mechanically, but getting it right first is cheaper. Change nothing else in the three handlers (decision 8). Commit subject: `Accept agent operation timing parameters.` |
| 3d | medium | sonnet | none | Register the five new declarations in `STRUCTURED_PARAMETERS` at `shakenfist/tests/external_api/test_openapi_spec.py:104`: `deadline_seconds` on `('/instances/{instance_ref}/agent/put', 'post', ...)`, `.../agent/get` and `.../agent/execute`, and `progress_timeout_seconds` on put and get only, each expecting `{'type': 'number', 'minimum': 0}`. The expected dict must describe the published shape **in full** — `test_structured_parameters_publish_their_real_shape` fails on a published constraint key the entry omits as well as on one it invents — so if a declaration ends up publishing a `maximum` the entry has to say so. Add a short comment above the group saying these are receipt-anchored second counts whose 0 is a sentinel rather than a floor, so a later reader does not "tidy" the minimum away. Then add `shakenfist/tests/external_api/test_agent_operation_parameters.py`, modelled closely on `test_snapshot_max_versions.py` (same `MockMariaDB` setup, same `config.NODE_UUID` placement trick, same `/auth` token dance), driving real requests at `/instances/<ref>/agent/execute` and asserting: a negative `deadline_seconds` is a 400; a non-numeric one is a 400; a valid one reaches `AgentOperation.new` with an absolute timestamp (assert against a patched `time.time`); an omitted one reaches it with the config default applied; an explicit `0` reaches it as `0.0`; and that `progress_timeout_seconds` on `/agent/execute` is a 400, because it is an undeclared parameter there. That last assertion is what pins decision 4 in code rather than in prose. Commit subject: `Test the agent operation timing parameters.` |
| 3e | medium | sonnet | none | Documentation and closeout. In `docs/developer_guide/api_reference/instances.md`, document the new request parameters on the three agent routes — the file already links them at line 653; add the parameter semantics next to that, including the three-valued meaning (omitted applies the server default, 0 means none, a positive value is seconds from request receipt) and the two config option names. Do **not** touch the response examples: `external_view()` already returned all four values as of phase 2 and they are already documented (decision 7). Check `docs/developer_guide/api_reference/agentoperations.md` for any request-side statement that is now incomplete. If `docs/operator_guide/` documents agent operation timeouts anywhere, the 900 second constant is still accurate until phase 4, so say the new options exist and take effect at creation while enforcement lands in phase 4, rather than rewriting the timeout story now. Then set phase 3 to `Complete` in the master plan's phase table and link this file, and correct `docs/plans/index.md`'s `3 of 8` to `4 of 8`. Commit subject: `Document agent operation timing parameters.` |

## Risks and mitigations

- **The falsy-zero bug.** `if not deadline_seconds` treats the
  client's explicit `0` sentinel as "omitted" and silently applies the
  600 second default, destroying the first-class no-deadline use case
  in a way no error message reveals. *Mitigation:* step 3b's brief
  names it; the helper tests cover explicit `0` for each parameter
  separately and together; step 3d asserts it end to end through a
  real request. A reviewer should grep the diff for `if not deadline`
  and `if not progress` and reject either.
- **Storing NULL for an omitted parameter.** Superficially tidy — "no
  intent, let the database default it" — and wrong, because it moves
  the deadline's anchor from request receipt to dispatch and quietly
  makes every new row look like it came from a pre-upgrade node.
  *Mitigation:* decision 1 and survey finding 1 both state it; step
  3d asserts an omitted parameter arrives at `AgentOperation.new` as
  an absolute timestamp, which fails if NULL is passed.
- **A declared bound with nothing behind it.** If the handler guard is
  dropped in review as "the validator will do it", the published
  `minimum: 0` becomes a promise the server does not keep for as long
  as `API_VALIDATION_MODE` stays at `warn` — which is where it is
  today and where `PLAN-api-input-validation.md` phase 4 leaves it
  until #3739 closes. *Mitigation:* decision 3; the 400 assertions in
  step 3d run with the config at its default, so they fail if the
  guard is removed regardless of validation mode.
- **Rolling-upgrade 400s for a newer client.** Survey finding 4: an
  API node that predates this phase answers 400 to a body carrying
  `deadline_seconds`. *Mitigation:* out of scope here, recorded in
  Future work for phase 6 to sequence — the client must not send the
  parameter unless it knows the cluster supports it, or must treat
  that 400 as a signal to retry without it.
- **Scope creep into enforcement.** The phase is small and phase 4 is
  adjacent and tempting. *Mitigation:* the definition of done includes
  a grep proving no enforcement consumer appeared, the same check
  phase 2 used.

## Definition of done

Runnable, from the repository root. The two python checks need the
project importable, so run them with `.tox/py3/bin/python` (or any
environment where `import shakenfist` works).

```sh
# 1. Both config options exist, with the decided defaults.
.tox/py3/bin/python -c "
from shakenfist.config import config
assert config.AGENT_OPERATION_DEFAULT_DEADLINE == 600
assert config.AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT == 30
print('config ok')"

# 2. Five declarations, with the right shape, and
#    progress_timeout_seconds absent from execute (decision 4). Read
#    off the decorated handlers rather than the served specification:
#    importing shakenfist.external_api.app builds the API and reaches
#    MariaDB, so the specification can only be rendered inside a test
#    with MockMariaDB. specs_dict is what swag_from stored, and
#    test_openapi_spec.py already proves the served specification
#    agrees with it. (Checked: this snippet runs today and fails with
#    "InstanceAgentPutEndpoint is missing deadline_seconds".)
.tox/py3/bin/python - <<'EOF'
from shakenfist.external_api import instance as api_instance

def body_props(name):
    for p in getattr(api_instance, name).post.specs_dict.get('parameters', []):
        if p.get('in') == 'body':
            return p['schema'].get('properties', {})
    return {}

EXPECTED = {'type': 'number', 'format': 'a floating point number',
            'minimum': 0}
for name, progress in (('InstanceAgentPutEndpoint', True),
                       ('InstanceAgentGetEndpoint', True),
                       ('InstanceAgentExecuteEndpoint', False)):
    props = body_props(name)
    wanted = ['deadline_seconds']
    if progress:
        wanted.append('progress_timeout_seconds')
    for param in wanted:
        assert param in props, '%s is missing %s' % (name, param)
        got = {k: v for k, v in props[param].items() if k != 'description'}
        assert got == EXPECTED, (name, param, got)
        assert props[param].get('description'), \
            '%s %s has no description' % (name, param)
    if not progress:
        assert 'progress_timeout_seconds' not in props, 'decision 4 reversed'
print('declarations ok')
EOF

# 3. No enforcement consumer appeared. Same check phase 2 used, and
#    for the same reason: matching attribute access rather than the
#    bare words, because 'deadline' occurs a dozen times in the
#    daemons as gRPC call-deadline prose.
test 0 -eq "$(grep -rnE '\.(deadline|progress_timeout|last_progress|attempts)\b' \
    shakenfist/daemons/ --include='*.py' \
    | grep -vc '^shakenfist/daemons/database/main.py:')" \
  && echo 'no enforcement consumer'

# 4. The 900 second constant is untouched -- it is phase 4's to remove.
grep -q 'AGENT_OPERATION_EXECUTION_TIMEOUT = 900' \
    shakenfist/daemons/sidechannel/main.py && echo 'constant intact'

# 5. Full check.
pre-commit run --all-files
```

By inspection, each falsifiable:

- Every "omitted" test in the helper is `is None`, and no line of the
  new code tests these two parameters for truthiness.
- A refused request creates no `AgentOperation` and adds no event: the
  error return precedes both in all three handlers.
- The three-valued meaning of each parameter is stated in the same
  words in the declaration description, the API reference, and the
  config option description — no page says something a different page
  contradicts.
- `docs/developer_guide/api_reference/instances.md` documents the
  request parameters and its response examples are unchanged from
  phase 2.

## Future work

- **A newer client against an older API node gets a 400.** Survey
  finding 4. `log_request` merges the body into handler kwargs and a
  key no handler accepts raises `TypeError`, so a pre-phase-3 API node
  refuses a request carrying `deadline_seconds` outright rather than
  ignoring it. During a rolling upgrade a cluster can have both. Phase
  6 must decide whether the client probes, version-gates, or retries
  without the parameter on a 400. Recorded here so phase 6 inherits
  the constraint rather than discovering it in CI.
- **`execute` has no progress signal at all.** Decision 4 stores
  `0.0`, which is true today. If `execute` ever streams output,
  `ExecuteCommand.reports_progress` becomes `True` and
  `progress_timeout_seconds` has to be added to that endpoint as a new
  parameter — additive and safe, but it means the parameter set across
  the three sibling endpoints is deliberately asymmetric until then.

## Back brief

Before implementing, confirm:

1. **Decision 4** — that `agent/execute` publishes `deadline_seconds`
   only, and stores `progress_timeout = 0.0`. This is the one
   asymmetry in the phase and it is cheap to reverse now and expensive
   after rows carry values and a client ships against the shape.
   Gated: do not start step 3c until this is agreed.
2. **Decision 3** — that out-of-range input is a 400 rather than
   clamped, departing from the events-`limit` precedent.
3. That step 3b's helper signature and return convention are what the
   three handlers want before three call sites exist.
