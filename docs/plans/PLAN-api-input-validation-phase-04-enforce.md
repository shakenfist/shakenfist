# Phase 4: Enforce

Phase 4 of [`PLAN-api-input-validation.md`](PLAN-api-input-validation.md),
following [phase 3](PLAN-api-input-validation-phase-03-compile-and-warn.md).

Phase 3 built the layer and rejected nothing. This phase makes it
reject, which is the first request-visible change in the plan. Three
declaration defects the warn window found are fixed first, the
derivation is taught to see the class of defect that produced the
larger of them, and the switch is thrown only after the log has been
watched going quiet.

**Planning effort:** high. The phase turns on the plan's one
irreversible-feeling decision (D10), changes status codes on paths no
test covers, and touches 55 endpoint declarations.

## Context

The measurement window opened 2026-08-13 and closed 2026-08-21. Its
result, in one line: **the layer is correct and the declarations are
not quite**. Across one full functional CI run and eight daily sfcbr
readings, every finding was explained, and exactly two of the five
signatures were declaration bugs rather than intended rejections.

| Population | n | Verdict |
|---|---|---|
| Intended rejections (negative tests) | 33 | Enforcement is the point |
| `unknown-parameter` `namespace` on ref lookups | 9 | Declaration bug, [#3739](https://github.com/shakenfist/shakenfist/issues/3739) |
| `type-mismatch` on metadata `value` | 22 | Declaration bug, found on the last day of the window |
| `body-path-collision` | 0 | Nothing observed |
| `unknown-parameter` reaching its handler | 0 | The population D10 turns on |

Both declaration bugs have the same shape — a declaration narrower
than the behaviour it describes — and both would become a 400 for a
working caller the moment enforcement is on. Neither was visible to
phase 1's audit, and one of them is invisible to it *by construction*.

## Scope

**In:**

* Declare the `namespace` body parameter on every handler behind a
  ref-resolving decorator (#3739).
* Teach `declarations.py` to see kwargs consumed by decorators, so
  that class of defect fails CI instead of being found by a warn log.
* A vocabulary token for a caller-opaque JSON value, applied to the
  fourteen metadata `value` declarations.
* A confirmatory warn reading on sfcbr with those fixes deployed.
* Flip `API_VALIDATION_MODE` to `enforce` by default.
* Documentation and a release note for the contract change.
* Functional CI coverage of the refusal shape.

**Out:**

* Enforcing `required` (D17). Phase 6 owns it.
* Semantic validation of the prose formats (`netblock`, `uuidorname`,
  `url`, `ipv4`, `node`, `namespace`). Phase 6.
* Narrowing `except TypeError` to JWT errors. Phase 5, gated on this.
* Folding the four hand-authored `get_args` schemas into the compiled
  path — the master plan's phase 4 line asks for this and the survey
  found the line is wrong about what it would mean. See D19.
* Response validation. Ruled out in phase 0 (D7).

## What the survey found

Every claim below was checked against the tree at `45debf843`.

**The enforcement code already exists and is tested.**
`validate_request` in `shakenfist/external_api/base.py:1570` already
has the `enforce` branch (`base.py:1644`), already filters
`missing-required` out of it, and already stashes findings *before*
the enforce decision so a refused request still emits its telemetry.
`API_VALIDATION_MODE` is a `Literal['off', 'warn', 'enforce']` at
`shakenfist/config.py:226`. Phase 4 is therefore mostly a
declaration-correctness phase with a one-word default change at the
end, which is not what the master plan's phase 4 line implies.

**#3739 is larger than the warn log showed, and it is precisely
countable.** Three decorators pop an undeclared `namespace` from
kwargs before the handler sees it:
`arg_is_instance_ref` (`base.py:901`), `arg_is_network_ref`, and
`_resolve_artifact_ref` (`artifact.py:57`, reached through both
`arg_is_artifact_ref` and `arg_is_visible_artifact_ref`). An AST walk
of `shakenfist/external_api/` finds **55 handler methods** behind one
of those four decorators, and **51 of them declare no body
`namespace`**. (The survey's first draft said none of them did. Four already had one — `InstanceEndpoint.get`/`delete` and
`NetworkEndpoint.get`/`delete` — passing the audit only because those
four handlers carry a vestigial `namespace=None` in their signature
which the decorator's pop guarantees is always `None`. Corrected here
during implementation.) The warn log saw nine findings because CI exercised one
route family; the exposure is all 55.

This is not a theoretical caller. `shakenfist_client/apiclient.py`
sends `{'namespace': ...}` in the body from `get_instance` (line 608),
`delete_instance` (812), `get_artifact` (873), `get_network` (995) and
`delete_network` (1007), gated on the `get-instance-namespace` /
`get-network-namespace` capability tokens. **Enforcing today would 400
the official client's own cross-namespace lookups.**

**Phase 1's audit cannot see this, and that is structural.**
`declarations.py` derives a handler's parameters from its signature
(`handler_kwargs`, line 408), its `@use_kwargs` schema
(`query_parameters`, 235) and its `flask.request.args` reads
(`request_args_parameters`, 348). A kwarg a *decorator* removes never
appears in any of the three. Nothing in the audit is wrong; the
derivation simply has no term for it. Fixing the 55 declarations
without fixing the derivation leaves the next `kwargs.pop` in a
decorator as the next enforcement outage.

**The metadata `value` finding is fourteen sites, not one.**
`grep -rn "'value', 'body'" shakenfist/external_api/` returns fifteen.
Fourteen are the metadata `value` parameter, every one declared
`string`: `auth.py:664,690`, `instance.py:1387,1536`,
`network.py:473,499`, `artifact.py:927,953`, `blob.py:402,427`,
`node.py:224,252`, `interface.py:153,180`. The fifteenth,
`network.py:712`, is an unrelated `value` declared `ipv4` and is
correct. The handlers pass the value straight to
`add_metadata_key()` (`baseobject.py:669`), which stores whatever it
is given, so every one of the fourteen declarations is narrower than
its handler.

**`ARGTYPES` has no token for "any JSON value."**
`shakenfist/external_api/base.py:359` carries eighteen tokens; the
widest structured ones are `dict` (`{'type': 'object'}`) and
`arrayofdict`. sfcbr's k3s orchestration traffic stored both a dict
and a list under the same parameter, so neither is wide enough. This
is the one genuinely new piece of vocabulary the phase needs.

**The master plan's phase 4 line is wrong about the `get_args` fold,
and this plan corrects it at source.** See D19. The master plan's
phase 4 row and the `docs/plans/index.md` row were both edited in the
same commit as this plan; a later step should not redo it.

**Phase 3's status was Complete in substance and `In progress` in
both tables.** The window closed in `b452b5761` (2026-08-21), the
D10 recommendation and the classified list are both written up, the
`test_every_documented_handler_compiles` criterion is enforced by
`shakenfist/tests/external_api/test_validation_compiler.py:35`, and
the kasm cron watcher and its state directory are gone. Only the
status columns were never flipped, and this plan's commit flips them.

**Nothing else in the master plan's phase 4 section was stale.** The
error shape (D4), the chain position (D3) and the warn-only exit
criterion (D5) all describe the code as it stands.

## Decisions

### D14. `unknown=RAISE`. Undeclared body keys are refused.

Phase 3 recommended this provisionally and said phase 4 would settle
it with counts in hand. The counts:

* **Reaches-handler population: 0.** No observed request carried an
  undeclared key *and* would have reached its handler. RAISE and
  today's behaviour never disagreed in the window.
* **Answered-first population: 9,** every one of them the `namespace`
  key of #3739 — a declaration bug this phase fixes in step 1. After
  step 1 that population is empty too.

So RAISE refuses nothing any observed caller sends. What it changes is
the *message*: today an undeclared key that reaches its handler is a
400 carrying `AuthEndpoint.post() got an unexpected keyword argument
'zzz'`, which is #3612 in its purest form. RAISE makes it
`{"error": "zzz: not declared by this endpoint", "status": 400}`.

`EXCLUDE` was the alternative and is rejected: silently dropping
`{"nmae": "x"}` would create an unnamed instance rather than telling
the caller they typed the parameter wrong, which is the defect class
this plan exists to close, arriving by a different door.

### D15. A new `any` token for caller-opaque values.

Body-only, like `dict` and the array tokens, because outside a body
there is no schema object to hold it. It renders as
`{'format': 'any JSON value'}` — a `format` annotation with no `type`,
so the published schema constrains nothing while still telling a
reader what the parameter is. If `openapi_spec_validator` objects to a
schema with no `type`, fall back to a bare `{}`; **the specification
validation test is the arbiter, not this paragraph.**

It compiles to `marshmallow.fields.Raw`, which the compiler already
falls back to for unrecognised tokens (phase 3's third review round
made that path drop published bounds, so `any` carrying no bounds is
already the supported case).

Applied to exactly the fourteen metadata `value` declarations named
in the survey. Not applied to `network.py:712`, and not applied to
anything else: the metadata family is the only place the API stores a
caller-supplied value it never interprets, and widening a declaration
is the one edit in this phase that *loses* validation.

### D16. The default flips to `enforce`.

Not an operator opt-in. The warn window was the price of earning the
switch, and leaving the default at `warn` would mean the defect class
stays open in every deployment while the machinery to close it sits
there unused. `off` and `warn` remain as the operator's valve, and the
release note names them as the rollback.

### D17. `missing-required` stays out of the enforcement decision.

This is already the code's behaviour (`base.py:1650`) and it is one
line away from being tidied into consistency by someone who has not
read why. Several parameters are declared `required` while omitting
them has always worked; enforcing that is phase 6's decision and
would break working callers. The request-level test that `enforce`
plus an omitted required parameter still reaches the handler is a
**required** deliverable of step 4, not an optional one.

### D18. `body-path-collision` is enforced.

Decision D8 said a body key overwriting a path parameter is rejected.
The window observed zero, so enforcing it costs nothing measured and
closes a real overwrite hazard. Recorded because it is the one reason
code being switched on without a population behind it.

### D19. The `get_args` fold is deferred, and the master plan's
description of it is corrected.

The master plan's phase 4 line reads "fold the hand-authored
`get_args` schemas into the compiled path". Read literally — delete
the four `@use_kwargs` decorators and let the compiled path take over
— **it is a bug, not a refactor.** The compiled path is check-only:
`validation.check()` returns findings and `validate_request` calls
through, never coercing or injecting. `log_request` merges the JSON
body into kwargs and nothing merges `flask.request.args`. So
`@use_kwargs` is the *only* mechanism by which a query-string
parameter reaches a handler at all. Deleting it from
`blob.py:196` would silently revert `offset` and `limit` to their
signature defaults on `GET /blobs/<uuid>/data` — reading a whole blob
where the caller asked for a range.

The defensible reading is the other one: keep `@use_kwargs` and
*generate* its schema from the same declaration list `swagger_helper`
receives, removing the second source of truth that phase 0's D2
called out. That is achievable — hoist the parameter list to a module
name, pass it to both, and build the marshmallow schema from the
shared token mapping — but it needs signature defaults at runtime and
it moves `blob.py`'s hand-rolled negative-offset check into a field,
which changes an error message. Two request-visible changes in the
phase that flips enforcement is exactly the shape that gave phase 3
four review rounds.

So: deferred, with an issue filed, and the master plan's phase 4 row
edited to say what folding actually means. Enforcement does not need
it — the compiled check runs first and answers 400 before the
hand-authored schema is consulted, so the duplication is inert, not
dangerous.

### D20. The derivation learns to see decorator-consumed kwargs.

Without this, step 1 is a sweep that nothing keeps true. `audit()`
gains a term: for each handler, walk the decorators applied to it,
resolve each to its definition in the same tree, and collect literal
`kwargs.pop('<name>', ...)` keys as parameters that must be declared
in the `body`. A decorator this cannot resolve is *reported*, not
skipped — the same "absence must not be indistinguishable from
success" rule the audit was rewritten around in phase 1.

This is what turns step 1's 55 edits from a one-off into an
invariant, and it is why step 2 exists as its own step rather than as
a line in step 1's brief.

### D21. No capability token for enforcement.

`API_CAPABILITIES` (`app.py:309`) exists and the client uses
`check_capability` for feature detection. Declined here: enforcement
is not a feature a client can use differently. A well-formed request
is unaffected, and a malformed one has no fallback path to take. The
release note is the right surface. Recorded so it is not re-proposed
in review.

### D22. The switch is thrown only after a confirmatory reading.

Steps 1 to 3 fix exactly the two signatures the window found. Landing
them and flipping the default in the same pull request means nothing
ever verifies they worked — the measurement would go dark at the
moment it stops being cheap to run.

So steps 1 to 3 merge, sfcbr redeploys in `warn`, and step 4 does not
merge until:

* one full functional CI run produces **zero** `unknown-parameter
  namespace` findings (that run is what produced the nine), and
* the `type-mismatch` on metadata `value` is absent across at least
  72 hours **during which the k3s orchestration traffic that produced
  it has actually run**. An absent signature whose generator did not
  run is not evidence, and phase 3's log records three separate
  occasions where a quiet channel was mistaken for a quiet system.

Whatever watches this window is exercised by hand before it is
trusted — phase 3's notifier failed silently for its last three runs
because cron's `PATH` could not find `sops`. If a notifier is used at
all, run it once from a cron-like environment and confirm the alert
arrives.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | high | opus | none | Fix #3739. Declare `('namespace', 'body', 'namespace', '<description>', False)` in the `swagger_helper()` parameter list of every handler method decorated with `arg_is_instance_ref`, `arg_is_network_ref`, `arg_is_artifact_ref` or `arg_is_visible_artifact_ref`. The survey counted **55** such methods across `agentoperation.py`, `artifact.py`, `instance.py`, `network.py` and `snapshot.py`; recount with an AST walk rather than trusting that number, and report if it differs. The decorators are `base.py:899` (`arg_is_instance_ref`), `base.py` (`arg_is_network_ref`) and `artifact.py:52` (`_resolve_artifact_ref`, reached via `arg_is_artifact_ref` at 131 and `arg_is_visible_artifact_ref` at 147); each does `kwargs.pop('namespace', None)` and passes it to `resolve_lookup_namespace()`. The description should say what the parameter *does* on that route — it widens or narrows which namespace a *name* resolves in, and is ignored for a UUID lookup on the artifact path — not merely "the namespace". Required is `False`: every one of these routes works without it today. Do not add it to handlers that do not carry one of those four decorators. `python3 tools/fix-api-parameter-locations.py --apply` is not the tool for this; these are additions, not location drift. Verify with `test_parameter_declarations.py` and `test_openapi_spec.py`. Commit subject: `Declare the namespace body parameter on ref lookups.` |
| 2 | high | opus | none | Close #3739's *class*. Teach `shakenfist/external_api/declarations.py` to derive the kwargs a handler's decorators consume, so a future `kwargs.pop` in a decorator fails CI instead of surfacing in a warn log. Add a function beside `handler_kwargs` (line 408) and `query_parameters` (235) that, for a given handler `ast.FunctionDef`, resolves each name in its `decorator_list` to a `FunctionDef` in the same package (the four in step 1 live in `base.py` and `artifact.py`, and `arg_is_artifact_ref`/`arg_is_visible_artifact_ref` both delegate to `_resolve_artifact_ref(func, widen=...)`, so the walk has to follow one level of delegation) and collects literal `kwargs.pop('<name>', ...)` keys. Wire it into `audit()` (553) so those names must be declared with location `body`, and into `derived_location()` (527) so the location is derivable rather than reported as underivable. **A decorator that cannot be resolved must be reported, not skipped** — the phase 1 rule that an opt-out must be visible. Read `docs/developer_guide/writing_an_endpoint.md` from line 294 ("What is not checked yet"), which documents the derivation's known gaps and must gain or lose an entry accordingly. After step 1 the audit must be clean; run it against `develop` *before* step 1's changes too and confirm it reports 55 problems, because an audit term that reports nothing when the defect is present is worse than no term at all. Commit subject: `Audit the kwargs that decorators consume.` |
| 3 | medium | sonnet | none | Add an `any` type token and widen the metadata `value` declarations. In `ARGTYPES` (`shakenfist/external_api/base.py:359`) add `'any': {'format': 'any JSON value'}` — deliberately no `type`, so the published schema constrains nothing. `swagger_helper()` refuses object and array tokens outside a body; `any` must be refused outside a body for the same reason. If `openapi_spec_validator` rejects a typeless schema, fall back to `{}` and say so in the commit message; `shakenfist/tests/external_api/test_openapi_spec.py` decides, not the plan. Then change these fourteen declarations from `string` to `any`: `auth.py:664,690`, `instance.py:1387,1536`, `network.py:473,499`, `artifact.py:927,953`, `blob.py:402,427`, `node.py:224,252`, `interface.py:153,180` — verify each is the metadata `value` parameter before editing. **Do not touch `network.py:712`**, which is a different `value` correctly declared `ipv4`. Check whether `STRUCTURED_PARAMETERS` in `test_openapi_spec.py:104` needs entries: its completeness is derived from the published specification, so a typeless schema may or may not register as structured — if it does not, extend the derivation so a token this wide cannot be added invisibly. Add a test pinning that exactly fourteen declarations use `any` and that they are the metadata family. Commit subject: `Declare metadata values as any JSON value.` |
| — | — | — | — | **Gate: back brief before step 4.** Steps 1-3 merge, sfcbr redeploys, and D22's confirmatory reading is taken and written into this plan's measurement log. Step 4 does not start until the operator has seen it. |
| 4 | high | opus | none | Flip `API_VALIDATION_MODE`'s default from `'warn'` to `'enforce'` in `shakenfist/config.py:226`, update its description, and update `shakenfist/tests/test_config.py:43-55`. Then write the tests that pin what enforcement means, all of them at request level through the real decorator stack (phase 3's review found two defects that unit tests missed precisely because they tested components in isolation) in `shakenfist/tests/external_api/test_request_validation.py`: an undeclared body key on a declared endpoint answers exactly `{"error": "<name>: not declared by this endpoint", "status": 400}` and the response body contains **no** interpreter text — assert the absence positively, not by eyeballing; `enforce` plus an omitted `required` parameter still reaches the handler (decision D17); a body key colliding with a path parameter is refused (D18); a finding on a request that would have succeeded refuses it, and a request with no findings is untouched. **Mutation-test each assertion**: break the code it covers and confirm the test fails. Do not remove or "tidy" the `missing-required` filter at `base.py:1650`. Commit subject: `Enforce the API parameter declarations.` |
| 5 | medium | sonnet | none | Documentation and release note. In `docs/developer_guide/writing_an_endpoint.md`, rewrite "What validation does with them" (line 262) and "What is not checked yet" (294): enforcement is on, `enforce` is the default, and the sections currently state the opposite in four places. Add the `any` token wherever the type vocabulary is listed. In `docs/release_notes/v07-v08.md`, add an entry under `## REST API` (line 30) covering: malformed input is now refused with `{"error": "<parameter>: <reason>", "status": 400}`; validation runs ahead of the per-method decorators, **so a request that is both malformed and refers to a missing or unauthorised object now answers 400 where it previously answered 404 or 403** — this is the contract change and it must be stated plainly rather than implied; `required` is still not enforced; and `API_VALIDATION_MODE=warn` or `off` is the rollback for an operator whose callers break. Check `docs/developer_guide/coding_rules.md` and `CLAUDE.md`'s "Parameter declarations are enforced" section for statements that enforcement is off. Commit subject: `Document API input validation enforcement.` |
| 6 | medium | sonnet | none | Functional CI coverage in `shakenfist/deploy/shakenfist_ci`. Add tests that a request carrying an undeclared body key is refused with a 400 whose error names the parameter and contains no Python interpreter text, and that a cross-namespace lookup passing `namespace` in the body — the exact `shakenfist_client` call pattern from `get_instance`/`get_artifact`/`get_network` — still works after step 1. The second is the regression test for #3739 and is the more important of the two: it is the thing that would have caught this before the warn window did. Follow the existing patterns in the CI suite; find a test that already asserts on an API error body rather than inventing a helper. Commit subject: `Test that malformed API input is refused.` |

## Progress

### Steps 1 and 2 were merged, because they cannot be separated

`test_declared_names_are_real_parameters`
(`shakenfist/tests/external_api/test_parameter_declarations.py:254`)
asserts that every declared parameter name appears in the handler's
own signature. The whole point of #3739 is that `namespace` never
does — the decorator popped it. So the 55 declarations of step 1 fail
that assertion 55 times until step 2's derivation term exists to
explain them, and step 2's term turns 51 handlers red until step 1's
declarations exist to satisfy it. Neither is green alone, and this
repository requires each commit to build and pass its tests.

They landed as one commit, `Declare the namespace parameter decorators
consume.` The alternative — 55 entries in `UNDECLARED_BY_DESIGN` for
one commit and their removal in the next — would have written the
exact exemption list this phase exists to shrink.

**The audit term was proven to fire, not assumed to.** With the
derivation in place and the declarations reverted, `audit()` reports
51 problems naming class, method and parameter, and no others; with
the declarations applied it reports none. Three mutations were added
to `tools/check-api-declaration-guards.sh` and all three are caught.

### Found while implementing, recorded rather than fixed

* **Mutation 4 of the guard harness had been silently defanged.** It
  deleted a handler's `swag_from` by pasting the decorator's full
  text; adding a parameter made the paste stop matching, so the
  mutation became a no-op while still reporting success. Rewritten to
  bound the deletion by line number. The harness has this fragility
  wherever a mutation matches source text it does not own, and the
  failure mode is the one this plan keeps meeting: a check that
  reports success because it could not find what it was looking for.
* **`requires_namespace_exist_if_specified` is dead on two routes.**
  `InstanceEndpoint.delete` and `NetworkEndpoint.delete` carry it
  *after* the ref decorator, which has already popped `namespace`, so
  its `kwargs.get('namespace')` is always `None`. The other ten uses
  are on creation and collection routes and work. Pre-existing, out of
  scope, untouched.
* **Four now-redundant `namespace=None` signature parameters** on the
  same four handlers that already declared the key. Harmless and never
  populated. Removing them is safe now that the derivation sees the
  decorator, and is not worth its own risk in this phase.
* **The `any` token needed the specification test's completeness
  derivation widened.** That derivation asked whether a published
  schema was an object or an array. A typeless schema is neither, so
  the widest token in the vocabulary would have been the one type
  change the table could never catch. It now also fires on a schema
  with no `type`, verified by removing a registered entry and watching
  the test fail.

### Two comments in the tree blamed this phase wrongly

Both are corrected, and both now point at issues instead.

* **`InstanceSnapshotEndpoint.post` and `thin`** (`snapshot.py:66` and
  the note above `UNDECLARED_BY_DESIGN`). Both said the
  absent-versus-false distinction waits on phase 4. It does not: the
  compiled path is check-only, so the handler receives `thin=False`
  from `log_request`'s body merge whatever the validation mode, and
  `'thin' in flask.request.json` draws the distinction today without
  any schema layer. The real blocker is that every shipped client
  sends the key unconditionally (`apiclient.py:706`), so honouring
  `false` needs a client release plus a capability token to detect it.
  Filed as
  [#4100](https://github.com/shakenfist/shakenfist/issues/4100).
* **The `get_args` fold** is
  [#4098](https://github.com/shakenfist/shakenfist/issues/4098), per
  D19, satisfying definition of done item 12.

### Added to step 4's scope: empty `UNDECLARED_BY_DESIGN`

The five `(*MetadataEndpoint, 'delete', 'value')` entries carry a
comment saying they are deferred to phase 4 and that "once the schema
layer rejects unknown parameters cleanly, this list should be empty".
That is correct, and this phase should finish it: the five handlers
accept a `value` kwarg on DELETE that none of them reads.

It belongs in **step 4 and not earlier**. Removing the kwarg while
validation is still in `warn` reintroduces exactly the leak the
comment warns about — a caller sending `value` on a metadata delete
gets `delete() got an unexpected keyword argument` as a 400. After the
D16 flip, D14's RAISE answers `value: not declared by this endpoint`
before the handler either way, so the signature cleanup becomes
correct rather than dangerous.

Checked before committing to it: the official client's
`_delete_metadata` (`apiclient.py:514`) sends no body at all, so no
shipped caller is affected. Two of the seven metadata delete handlers
were cleaned up already, so the pattern is proven.

### Review round 1 on [#4101](https://github.com/shakenfist/shakenfist/pull/4101)

Ten items: 2 `fix`, 1 `document`, 4 `consider`, 3 `none`. All seven
actionable items were taken, which is more than the exit rule asks
for. The reasoning, since taking every `consider` is normally the
wrong move: three of the four were one edit each, and the fourth
shrank the diff.

**Both `fix` items were the same fault this phase keeps finding: a
check that reports success without meaning it.**

* **Mutation 32 deleted a whole handler.** It searched for the
  namespace tuple by its nine-space indentation, which is a substring
  of the twelve-space declaration in `InstanceSnapshotEndpoint.post`
  above it. The search matched `post`, the terminator search ran on,
  and the splice removed the rest of `post`'s parameters, its
  responses, all five decorators, its body, and the head of `get`'s
  declaration. The result still parsed, so the harness reported a
  catch — of "a handler vanished", not of the defect the mutation
  names. Rewritten to anchor on `get`'s summary and bound every later
  index to the region after it, with three assertions which refuse
  the write rather than corrupting the tree; the old escaping splice
  now trips assertion B. Verified by diffing the mutation applied to
  a copy: one tuple, nothing else.
* **`any` compiled through the unrecognised-type fallback.**
  `_field()` reads `spec.get('type')`, and `any` deliberately has
  none, so every one of the fourteen sites took the branch whose only
  output is a warning saying the published specification contains a
  type the compiler does not know. Fourteen of those at every sf-api
  start, measured; a genuinely unrecognised token would have been
  indistinguishable from the expected noise. The rendering moved to
  `validation.ANY_VALUE_FORMAT` and `ARGTYPES['any']` is built from
  it, so recognition and rendering are the same string by
  construction. Measured after: **zero** warnings across 139 compiled
  schemas.

**One `consider` was stronger than reported.**
`_resolve_artifact_ref` carried an `if 'artifact_uuid' in kwargs:`
branch. The reviewer suggested declaring it, since the new derivation
cannot see a *read* the way it sees a pop. It is worse than
undeclared: no handler anywhere in `shakenfist/external_api/` accepts
an `artifact_uuid` kwarg and no route mounts one, verified by AST
walk, so the branch resolved the artifact and then called a handler
which raised `TypeError` — a 400 carrying interpreter text. Declaring
it would have published a parameter that never worked. Deleted, with
`test_artifact_uuid_is_not_a_parameter` pinning the property that
made it dead rather than the branch's absence.

The other three: the four namespace descriptions were pasted inline
at 55 sites, so a wording fix was 55 edits and drift between copies
was invisible — hoisted into `api_base` with
`test_namespace_descriptions_match_their_decorator` asserting the
pairing per handler and pinning the count at 55, which took ~145
lines *out* of the endpoint modules. The widened artifact description
omitted that `from_db_by_ref_visible_to` searches the caller's own
namespace first and wins, including answering 400 on an ambiguous
name there rather than widening. And the gap list in
`writing_an_endpoint.md` went from three entries to five.

**Recorded, not fixed.** Eight `sed` mutations in the guard harness
(1, 6, 7, 8, 10, 11, 15, 22) match several `blob.py` lines and mutate
all of them identically, so the intended defect is created at four to
eight sites instead of one. Fragile rather than wrong — a multiplied
identical defect still proves the guard fires for the reason claimed,
which is exactly what mutation 32 did not do.

**For step 5.** The release note must say that the specification now
documents a body on GET and DELETE read routes. That is not new
behaviour — the ref decorators have always popped `namespace` there —
but it is newly *visible*, and a reader who takes the published
specification as the contract will see a body appear on fifty-five
routes that had none.

## Risks and mitigations

**A caller nobody measured sends an undeclared key.** One sfcbr
tenant set and one CI suite is not every caller. *Mitigation:*
`API_VALIDATION_MODE=off` and `warn` remain, named in the release
note as the rollback, and require no code change to reach. The
confirmatory reading of D22 is the check, and the operator reads it.

**Status codes move on paths no test covers.** A request that is both
malformed and refers to a missing object now answers 400 where it
answered 404. *Mitigation:* stated in the release note as a contract
change (step 5), not left to be discovered in a bug report. Note for
review: this is not an information leak — validation runs *after*
authentication, so the caller learns only that their own request was
malformed.

**Step 1 is a 55-site sweep and sweeps put things in the wrong
place.** *Mitigation:* step 2's derivation is the arbiter. It derives
the required declarations from the decorators themselves, so a
declaration added to a handler that does not carry one of the four
decorators, or missing from one that does, fails CI. Step 2's brief
requires running the new audit term against unmodified `develop` and
seeing it report 55, so the check is proven to fire.

**`any` is a token that turns validation off for a parameter.** Once
it exists, it is the easy answer to any inconvenient type error.
*Mitigation:* step 3 adds a test pinning the exact set of
declarations that use it, so a fifteenth use is a deliberate,
visible edit.

**The confirmatory window is watched by something that fails
silently.** Three times in phase 3. *Mitigation:* D22 requires the
notifier be exercised from a cron-like environment before it is
trusted, and requires the metadata signature's *generator* to have
run, so absence is evidence rather than silence.

## Definition of done

1. Every handler behind a `namespace`-popping decorator declares a
   body `namespace`, and this is enforced by
   `test_parameter_declarations.py` rather than asserted here.
2. `declarations.audit()` reports zero problems on the tree, and
   reports 55 when run against `develop` before step 1's edits.
3. Exactly fourteen declarations use the `any` token, all of them the
   metadata `value` parameter, pinned by a test.
   `network.py:712` still declares `ipv4`.
4. `config.API_VALIDATION_MODE` defaults to `'enforce'`, and
   `test_config.py` asserts that default.
5. A request-level test proves an undeclared body key answers
   `{"error": "<name>: not declared by this endpoint", "status": 400}`
   and that the response body contains no interpreter text; the
   assertion has been mutation-tested.
6. A request-level test proves `enforce` plus an omitted required
   parameter still reaches the handler.
7. A functional CI test performs a cross-namespace lookup with
   `namespace` in the body and succeeds.
8. The published specification validates with zero errors
   (`test_openapi_spec.py`).
9. D22's confirmatory reading is written into the measurement log
   below, naming the CI run and the traffic that would have produced
   each of the two signatures.
10. No fact about enforcement is stated differently in
    `PLAN-api-input-validation.md`, this plan,
    `docs/developer_guide/writing_an_endpoint.md`,
    `docs/release_notes/v07-v08.md` and `CLAUDE.md`.
11. `pre-commit run --all-files` is clean.
12. An issue exists for the deferred `get_args` fold (D19), linked
    from the master plan. **Done: #4098.**
13. `UNDECLARED_BY_DESIGN` in `test_parameter_declarations.py` is
    empty, and the five metadata delete handlers no longer accept a
    `value` kwarg they never read.

## Back brief

Before executing any step, back brief the operator on the
understanding of this plan and how the intended work aligns with it.

There is a second, mandatory gate: **step 4 is not started until the
operator has seen D22's confirmatory reading.** Flipping the default
is the phase's only irreversible-feeling act, and the whole argument
for it rests on two signatures having gone quiet after steps 1 to 3.
That evidence is cheap to gather and expensive to fake, so it is
gathered and shown before the switch, not after.

## Measurement log

*(D22's confirmatory reading goes here, in the format phase 3's log
established: the reading command, what ran, and the signature table.)*
