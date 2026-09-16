# Declarative validation and a consistent error contract for the REST API

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read
relevant source files, understand existing patterns (the
decorator chain in `shakenfist/external_api/base.py`, especially
`Resource.method_decorators`, `log_request`,
`handle_authorization_exceptions`, `record_exception` and
`suppress_exceptions_to_client`; the `swagger_helper()`
declarations on every endpoint; the four existing
`use_kwargs` sites; `shakenfist/util/exceptions.py`). Ground
your answers in what the code does today. Do not speculate when
you could read it instead. Where a question touches on external
concepts (webargs / marshmallow schema composition, Flask-RESTful
dispatch, OpenAPI 2.0 parameter locations, HTTP status code
semantics for malformed input), research as needed to give a
confident answer. Flag any uncertainty explicitly rather than
guessing.

All planning documents should go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the component inventory and
`CLAUDE.md` for build commands, project conventions, and the
warning about decorator ordering in `external_api/app.py`.

When we get to detailed planning, I prefer a separate plan
file per detailed phase, named with `-phase-NN-descriptive`
appended.

I prefer one commit per logical change, and at minimum one
commit per phase. Each commit should be self-contained.

**Status: phases 0 to 6 planned; 0, 1, 2, 3, 4, 5 and 6 complete.**
The open questions at the bottom are answered in the Decisions
section; see
[`PLAN-api-input-validation-phase-00-decisions.md`](PLAN-api-input-validation-phase-00-decisions.md)
for the measurements behind them,
[`PLAN-api-input-validation-phase-01-declaration-audit.md`](PLAN-api-input-validation-phase-01-declaration-audit.md)
for what the audit found,
[`PLAN-api-input-validation-phase-02-type-vocabulary.md`](PLAN-api-input-validation-phase-02-type-vocabulary.md)
for what the vocabulary work shipped and the four deviations it
recorded, and
[`PLAN-api-input-validation-phase-03-compile-and-warn.md`](PLAN-api-input-validation-phase-03-compile-and-warn.md)
for the measurement that closed it, and
[`PLAN-api-input-validation-phase-04-enforce.md`](PLAN-api-input-validation-phase-04-enforce.md)
for the reading that unlocked the flip and what the flip changed for
callers, and
[`PLAN-api-input-validation-phase-05-narrow.md`](PLAN-api-input-validation-phase-05-narrow.md)
for the narrowing and the two leaks it closed, and
[`PLAN-api-input-validation-phase-06-required.md`](PLAN-api-input-validation-phase-06-required.md)
for the required-ness decision and what the semantic type tokens turned
out to be enforcing. Phase 6's survey split the phase it was planning:
what was one row covering required-ness and four semantic issues is now
phase 6 for the scalar half and phase 7 for element schemas on `dict`
and `arrayofdict`, which #528 needs and which nothing in the vocabulary
can express today. The push audit moves to phase 8. Neither phase 7 nor
phase 8 is yet cut into a per-phase file.

## Situation

The REST API does not validate its inputs. A request body value
of any JSON type reaches the handler that reads it, and what
happens next depends entirely on whether that particular handler
happened to guard the conversion.

The mechanism is two decorators in
`shakenfist/external_api/base.py`, both applied to every endpoint
through `Resource.method_decorators`:

* `log_request` merges **every key of the JSON request body**
  into the handler's kwargs verbatim, with no type checking and
  no schema (`base.py:588-598`). A body key also silently
  overwrites a same-named URL path parameter.
* `handle_authorization_exceptions` catches **any** `TypeError`
  raised anywhere below it and returns
  `400 <str(exception)>`. So a `TypeError` raised arbitrarily
  deep in the stack is handed to the client as the interpreter's
  own error text.

Anything that escapes both — a `ValueError`, an `OverflowError`,
an `OSError` — falls through to `suppress_exceptions_to_client`
and becomes a 500 with an exception repr in the body and a
recorded server exception on disk.

Issue #3609 is one instance: `{"limit": "5"}` on the events
endpoints produced
`400 "'<=' not supported between instances of 'str' and 'int'"`.

### Why the per-endpoint fix was abandoned

PR #3610 fixed #3609 by adding a shared `coerce_int()` helper and
routing seven call sites through it. It was closed unmerged after
six rounds of review. Two findings from that attempt matter here,
because they are the argument for doing this declaratively:

1. **Hand-rolled guards get written wrong.** Of seven guards
   written by hand, two were wrong on the first attempt: one
   caught `(TypeError, ValueError)` and missed `OverflowError`
   (reachable because Python's JSON parser accepts the
   non-standard `Infinity` literal, and `int(float('inf'))`
   raises `OverflowError`), and one type-checked
   `max_versions` without range-checking it, leaving a
   silently destructive negative value.
2. **The class was still not covered when the PR was
   abandoned.** Of the 13 parameters this API *declares* as
   `integer`, 6 were still read unconverted, including `cpus`
   and `memory` on `POST /instances`, which are passed to
   `Instance.new()` as received.

Every endpoint added in future inherits the defect by default,
because the default is no validation.

### The asset nobody is using

**124 of the 129 handler methods (96%) already declare their
parameters.** Every `swag_from(api_base.swagger_helper(...))`
call passes a list of

```
(name, location, type, description, required)
```

tuples — 254 parameters in total across 20 files, with 113
declared `body` and 118 declared `query`. `swagger_helper()`
already maps the type token through an `argtypes` table
(`base.py:141-160`) covering `string`, `integer`, `number`,
`boolean`, `uuid`, `uuidorname`, `namespace`, `node`, `url`,
`ipv4`, `dict`, `arrayofdict`, `arrayofstring`, `binary`,
`bearer`.

That is a schema. It is read by nothing except the documentation
generator.

Making it load-bearing is the cheap path to closing #528: no new
per-endpoint schemas need to be authored for the 96% that already
declare, and webargs is already a dependency doing exactly this
job at four sites (`blob.py`, `network.py`, `artifact.py`,
`instance.py`) — none of them on request bodies. All four were
`location='query'` when this was written; three are now
`location='json_or_query'`, the custom loader #3629 introduced
(see D6 below).

### The catch, stated up front

Because nothing has ever read these declarations, nothing has
ever checked them. Phase 0 measured the damage: parameter
*names* are 97% accurate, but parameter *locations* are not.

**116 of the 119 parameters that appear in a URL path are
declared as something other than `path`** — 104 as `query`, 11
as `body`, and one as `'qeury'`, a typo. Only 3 are correct. A
second declaration says `'post'`, which is not an OpenAPI
location at all. Since location is exactly what a parser uses to
decide *where to look for a value*, compiling these as written
would look for `artifact_ref` in the query string of a request
that carries it in the path.

This is less alarming than it sounds, because the mounted routes
in `app.py` are ground truth: which names are path parameters is
derivable, not a matter of judgement, so the fix is mechanical
rather than an audit. But it makes the declaration audit a
**precondition** for compiling, not a tidy-up that can follow it.

`required` is a separate and probably worse problem: `mode` on
`POST /instances/<ref>/agent/put` is declared required while
omitting it has always been accepted, so enforcing required-ness
naively would break working clients.

**The plan must therefore treat "the declarations are accurate"
as a hypothesis to be tested, not an assumption.** The mechanism
for testing it is a warn-only mode that validates, logs what it
*would* have rejected, and changes nothing — deployed to sfcbr,
which carries real traffic and ships logs to Loki.

### Type vocabulary gaps

The existing vocabulary is too coarse in at least these ways,
each of which is a real defect in the issue list:

* **No unsigned integer.** Artifact version indexes and
  `max_versions` must not be negative. A negative `max_versions`
  is silently destructive: `Artifact.delete_old_versions()`
  computes `sorted(indexes)[:-max]`, so `-1` deletes the oldest
  version on every index add.
* **No bounded integer.** The events `limit` has a documented
  default of 100 and a cap of 1000; `offset` on blob reads and
  upload truncate must be non-negative and, for truncate, within
  the object.
* **No format-constrained string.** MAC addresses (#534, since
  fixed outside this plan), base64-encoded user data (#3269),
  netblocks that must not overlap reserved ranges (#323).
* **No structured value types.** Disk, network and video specs
  are validated imperatively today (#528).

## Reported issues this plan addresses

Found by scanning all 83 open issues. The core group is the
reason for the plan; the error-contract group is the other half
of "what does a caller see when they get it wrong", and it shares
the same decorator chain.

### Core: declarative validation

| Issue | Filed | Summary |
|-------|-------|---------|
| [#528](https://github.com/shakenfist/shakenfist/issues/528) | 2020-11-11 | **Broaden declarative type/validity checking across all API endpoints.** The parent issue; explicitly notes only ~4 endpoint files use `use_kwargs` today. |
| [#936](https://github.com/shakenfist/shakenfist/issues/936) | 2021-09-02 | Replace hand-rolled instance-create validation with a declarative schema. Video/disk/network specs are validated imperatively in `instance.py`. **Closed 2026-09-12 as a duplicate of #528**, which now carries this work; phase 7 is where it lands. |
| [#3612](https://github.com/shakenfist/shakenfist/issues/3612) | 2026-08-03 | Body parameters merged into handler kwargs untyped; broad `except TypeError` returns interpreter messages. The mechanism description above. |
| [#3609](https://github.com/shakenfist/shakenfist/issues/3609) | 2026-08-02 | `GET /nodes/<node>/events` with a string `limit` returns a 400 containing a Python type error. The trigger. |
| [#534](https://github.com/shakenfist/shakenfist/issues/534) | 2020-11-12 | Validate MAC address *format* on interface create (uniqueness is already enforced). **Closed 2026-09-12 by [#4183](https://github.com/shakenfist/shakenfist/pull/4183)**, not by this plan — see the note below. |
| [#3269](https://github.com/shakenfist/shakenfist/issues/3269) | 2026-06-13 | Enforce base64-encoded user data at the API instead of failing later on the hypervisor with a traceback. |
| [#323](https://github.com/shakenfist/shakenfist/issues/323) | 2020-09-26 | Reject virtual networks that overlap reserved ranges (e.g. the floating network). |

### Error contract and attribution

| Issue | Filed | Summary |
|-------|-------|---------|
| [#3523](https://github.com/shakenfist/shakenfist/issues/3523) | 2026-07-26 | A bare `KeyError` surfaces as a generic "Server error" whose traceback shows only wrapper frames, plus a content-free paired WARNING. |
| [#3371](https://github.com/shakenfist/shakenfist/issues/3371) | 2026-07-11 | `record_exception()` logs tracebacks only at DEBUG, so ~35k traceback lines/day never reach centralised logging. |
| [#3615](https://github.com/shakenfist/shakenfist/issues/3615) | 2026-08-03 | `log_request` assigns a string over the whole header dict when redacting Authorization, so every authenticated request logs no headers at all. |
| [#3606](https://github.com/shakenfist/shakenfist/issues/3606) | 2026-08-02 | `JWT token has incorrect nonce` logged at ERROR with no client, namespace or endpoint context (40 events/14d on sfcbr). |
| [#2094](https://github.com/shakenfist/shakenfist/issues/2094) | 2023-11-23 | `DELETE .../route/<addr>` returns a bare 403 when the address is already unrouted; make it idempotent or friendlier. |

### Enabling

| Issue | Filed | Summary |
|-------|-------|---------|
| [#3616](https://github.com/shakenfist/shakenfist/issues/3616) | 2026-08-03 | Add `external_api/base.py` to the mypy target list. The validation layer lands here; it should be type-checked. |

### Adjacent — related, deliberately not absorbed

* [#1974](https://github.com/shakenfist/shakenfist/issues/1974) — pagination for listing endpoints. It needs exactly the bounded `limit`/`offset` parameter types this plan defines, and the events `limit` is a special case of it, but the query and response-shape work belongs to
  [`api-query-batching-roadmap.md`](api-query-batching-roadmap.md).
  **Coordinate on the parameter types; do not merge the plans.**
* [#3271](https://github.com/shakenfist/shakenfist/issues/3271) — bare image shorthand silently shadows a local label. Input *interpretation* rather than input *validity*; the resolution-order fix is independent.
* [#3373](https://github.com/shakenfist/shakenfist/issues/3373) — the mariadb gRPC client conflates "unavailable" with "not found". Same family (the wrong thing is surfaced to the caller) but a different layer, and already scoped elsewhere.
* [#3308](https://github.com/shakenfist/shakenfist/issues/3308) — the ansible collection's networkspec parser makes every non-empty value truthy. A *consumer* of this contract, and a good end-to-end test of it, but fixed in the collection.
* [#764](https://github.com/shakenfist/shakenfist/issues/764) and [#121](https://github.com/shakenfist/shakenfist/issues/121) — validating fetched image content. Validation, but of downloaded bytes rather than API input. Out of scope.

## Decisions

Resolved by phase 0. The measurements behind each are in
[`PLAN-api-input-validation-phase-00-decisions.md`](PLAN-api-input-validation-phase-00-decisions.md).

**The central hypothesis held.** 229 of 236 declared parameter
names (97%) match a kwarg their handler actually accepts, and no
declared type contradicts its signature default. The
declarations are good enough to compile.

1. **Library — webargs + marshmallow.** All three candidates
   (webargs, marshmallow, pydantic) are already pinned direct
   dependencies, so this is a fit decision, not a dependency
   one. webargs exists to parse Flask request arguments, is
   already used at four sites, and does per-location parsing.
   Pydantic models persisted state here; keep that boundary.
2. **Compile the existing declarations,** with a per-endpoint
   override for cases a declaration cannot express. Authoring
   254 schemas to replace declarations that are already correct
   is make-work, and the four endpoints that hand-author schemas
   today are exactly the ones whose documentation has drifted.
3. **Validation runs at index 0 of `method_decorators`** —
   verified empirically as the innermost position, so after
   authentication and before every per-method decorator. webargs'
   default 422 handling must be replaced so failures come out
   through `sf_api.error`.
4. **The error shape does not change:**
   `{"error": "<parameter>: <reason>", "status": 400}`. The
   official client never parses the message, and only 11 test
   assertions touch error text.
5. **Warn-only ends when every remaining rejection is
   intended** — not after a fixed duration. The window must
   cover a full functional CI run plus seven days of sfcbr.
   Warn records carry the offending value's *type*, never its
   value.
6. **Query strings become an accepted fallback** for parameters
   declared `query`, with the JSON body still authoritative.
   Additive and unbreakable: the client sends everything in the
   body regardless of method.
7. **Response validation is out of scope,** not deferred. It
   breaks working clients when wrong, and no issue asks for it.
8. **A body key colliding with a path parameter is rejected.**
   `log_request` already dodges one instance of this by mapping
   body `uuid` to `passed_uuid`, which shows it is a known
   hazard rather than a feature.
   *Amended by phase 3:* the decision stands, the supporting
   evidence does not. `passed_uuid` occurs once in the tree — the
   assignment itself — so no handler accepts it and the remap
   dodges nothing; it converts a body `uuid` into a guaranteed 400
   on every endpoint. The check also cannot live where D3 puts the
   validator, because `log_request` runs first and has already
   merged the body. See D11 and D12 in
   [phase 3](PLAN-api-input-validation-phase-03-compile-and-warn.md).
9. **The type vocabulary gains tokens and an optional
   constraints element** (`unsignedinteger`, `macaddr`,
   `base64`, `netblock`; `minimum` / `maximum` / `pattern`).
   These are valid Swagger 2.0 keywords, so constraints render
   into the published OpenAPI instead of being invisible to
   callers the way the events `limit` cap was.

### Phases

| Phase | Status | Description | Merged |
|-------|--------|-------------|--------|
| 0: Research and decisions | Complete | Measured declaration accuracy; chose webargs, compilation, chain placement, error shape, warn-only criterion. See [phase 0](PLAN-api-input-validation-phase-00-decisions.md) | `25e03b764` (#3620) |
| 1: Declaration audit | Complete | Correct 116 path-parameter locations from the route table, 2 invalid location tokens, 5 wrong names (incl. `sshkey`/`userdata` in the published OpenAPI) and 20 undeclared parameters; make `swagger_helper()` reject unknown locations; add a test that keeps declarations honest. A precondition for phase 3, and a documentation-correctness fix worth landing on its own merits. See [phase 1](PLAN-api-input-validation-phase-01-declaration-audit.md) | `25e03b764` (#3620) |
| 2: Type vocabulary | Complete | The specification-validation test (#3626) plus `schemes`/`securityDefinitions` template fixes; one schema-carrying body parameter per operation, taking the validation error count from 129 to zero; `unsignedinteger`/`macaddr`/`base64`/`netblock` tokens and the optional constraints element, rendered into the published OpenAPI so bounds like the events `limit` cap are visible to callers. See [phase 2](PLAN-api-input-validation-phase-02-type-vocabulary.md) | `ad759f25e` (#3666), `e9b28a65a` (#3685) |
| 3: Compile and warn | Complete | Code landed 2026-08-13 via #3726: declarations compiled to schemas, warn-only validation ahead of the handlers; four further decisions (D10-D13) recorded in the phase plan, including that an undeclared body key is *already* a 400 carrying interpreter text. The measurement window opened the same day and closed 2026-08-21 with every finding explained: 33 intended rejections, and two declaration bugs — the undeclared `namespace` of #3739 and fourteen metadata `value` declarations narrower than their handlers — which phase 4 fixes before it enforces. See [phase 3](PLAN-api-input-validation-phase-03-compile-and-warn.md) | `3790aa487` (#3726), `0c7eacf48` (#3742), `6274cd924` (#3835) |
| 4: Enforce | Complete | Landed 2026-09-09 via [#4141](https://github.com/shakenfist/shakenfist/pull/4141). Fixed the two declaration bugs the warn window found (#3739's undeclared `namespace` on 55 handlers, and fourteen metadata `value` declarations), taught the derivation to see decorator-consumed kwargs so that class cannot recur, and turned on rejection with one malformed-input response shape that never contains interpreter text. The `get_args` fold moved to Future work as [#4098](https://github.com/shakenfist/shakenfist/issues/4098): read as "delete the four `@use_kwargs` decorators" it is a bug, because the compiled path is check-only and `@use_kwargs` is the only thing that gets a query parameter to a handler. See [phase 4](PLAN-api-input-validation-phase-04-enforce.md) | `1c203b111` (#4101), `f1040a23b` (#4141) |
| 5: Narrow the handlers | Complete | Deleted the `except TypeError` arm from `handle_authorization_exceptions` (D23): a handler-internal `TypeError` is now a recorded 500 like any other server fault, and under the `warn`/`off` rollback an undeclared body key gets that same 500 instead of the 400-with-interpreter-text it used to (D25). Fixed [#3523](https://github.com/shakenfist/shakenfist/issues/3523) (a partial `cpuinfo` probe raising `KeyError` in `get_user_agent`) and removed two `requires_namespace_exist_if_specified` applications phase 4 found dead and never filed (F8). Grew a sixth step mid-phase, after step 2 found the generic 500 body still carried `repr(e)` (F11): every 500 response now answers a bare `server error` with no exception detail at all, for any cause (D31). Filed [#4161](https://github.com/shakenfist/shakenfist/issues/4161) for the proxy path answering an unreachable node with a 500 (F9) — re-verifying the finding before filing found that an unrelated fix ([#3743](https://github.com/shakenfist/shakenfist/issues/3743)) had already closed its largest instance, so the issue is scoped to what is still true rather than to the table as originally surveyed. See [phase 5](PLAN-api-input-validation-phase-05-narrow.md) | `b3de0a44f` (#4162) |
| 6: Required and scalar semantics | Complete | A sweep drove one real request per declaration through the whole decorator stack: of the 75 body/query declarations carrying `required=True` (every one had a handler default, so none was structurally required), 63 were already refused by a handler guard, 7 reached a null-hostile constructor and 500'd, and only 6 were accepted outright — of those, only `shared` on `POST /artifacts` was genuinely optional and moved to `required=False`; the other 5 committed a broken operation on omission (a queued agent command with a null path, a DNS record pointing at null) and kept `required=True` so enforcement turns that into a 400 instead. The `MISSING_REQUIRED` filter in `validate_request` was then deleted, so an omitted or explicit-null required parameter now answers 400 naming it, in every mode but `warn`/`off`. Separately, five of the nine semantic type tokens (`byte`, `a CIDR netblock`, `an IPv4 address as a string`, `url`, `uuid`) were given real validators keyed on the exact `format` string they publish, closing #3269 by rejecting non-base64 `user_data` at the API instead of on the hypervisor; `macaddr` already validated via PR #4183 and is unchanged. `POST /networks` gained a handler guard refusing a netblock that overlaps the deployed floating network, closing #323. #534 was closed outside this plan by #4183 before the phase started (see the note below). See [phase 6](PLAN-api-input-validation-phase-06-required.md) | `81aa9a7d0` (#4199) |
| 7: Structured parameter schemas | Complete | Taught the type vocabulary to describe what is *inside* a structure, and then described three. `_field()` gained an object branch, so a rendered `properties` block compiles to a nested marshmallow schema with `unknown=RAISE`, and `ARGTYPES` gained `diskspec`, `arrayofdiskspec`, `networkspec`, `arrayofnetworkspec` and `videospec`, built from three module-level constants so a shape declared twice cannot drift. The population was six `dict`/`arrayofdict` declarations across four operations, of which two stayed out and are documented as staying out: `metadata` is free-form by design and `bound_claims` is already guarded by hand with better messages than a schema could produce. Twenty-one nested values which were a recorded 500 or a silent acceptance now answer 400 naming the key and its index (`disk[1].size: Not a valid integer.`) — eleven that faulted, ten that were accepted — while five values which look like they should have been narrowed are deliberately still accepted, because a validator narrower than its handler is a breaking change wearing the clothes of a correctness fix. Three real narrowings were taken and written down: a `disk[].type` outside `disk`/`cdrom`, a caller-supplied `blob_uuid` inside a diskspec, and a diskspec asking for neither a `size` nor a `base`. Three of the new guards are handler guards rather than schema checks and so are not rolled back by `API_VALIDATION_MODE=warn`, deliberately: the size-or-base guard; a `networkspec` whose `network_uuid` is an explicit `null`, which previously resolved to an arbitrary network in the namespace and on interface hotplug answered 200 and created an interface on a network the caller never named; and a `videospec` whose `model` or `memory` is an explicit `null`, which the review found the phase had missed because those two checks were still presence tests, so a null was stored and rendered into the domain XML as `type='None'`. The review also found the phase had published `network[].float` as a boolean while reading it with a bare truthiness test on the raw body, so `"false"` floated the interface; `validation.declared_boolean()` is now the single reading, keyed on marshmallow's own truthy and falsy sets. That defect is the lookup function's rather than the vocabulary's, so it was filed as [#4223](https://github.com/shakenfist/shakenfist/issues/4223) and left for its own fix, carrying `automated-fix-attempted` from the moment it was filed. Decision D46 was rewritten mid-phase (marshmallow's `strict=True` refuses a query parameter, which arrives as a string on the wire) and the rewrite silently invalidated three other statements reasoning from it, which four separate steps rediscovered independently — the phase's own recorded lesson, and the reason its close-out re-read the decisions as a set. See [phase 7](PLAN-api-input-validation-phase-07-structured.md) | — |
| 8: Push audit | Not started | Runs `PUSH-AUDIT.md` over the accumulated diff of every phase in this plan against `develop`, not the last phase's diff alone. Findings land as their own pull request, and the plan is not complete until each is resolved or declined in writing here; if the audit finds nothing, that is recorded in one sentence | — |

The `Merged` column records what put each phase on `develop`.
These entries were reconstructed after the fact, because the plan
did not record them as its phases landed; they come from the
repository's merged pull request list cross-checked against the
first-parent history, and not from a path-filtered `git log`,
which cannot say which commits arrived inside a pull request.
Every SHA is the merge commit of the pull request named beside
it, so `<sha>^1..<sha>` is the whole of what that pull request
put on `develop`. A phase which has not landed reads `—`.

Phases 0 and 1 shared a pull request: #3620 carried the master
plan, the phase 0 decisions and the declaration audit together.
Phase 4 landed across two pull requests, #4101 and #4141.

### Where the tracked issues stand

Recorded here rather than by editing the tables above, so there
is one place to maintain and the tables stay a record of what
the plan was scoped against.

**Closed since the plan was written:** #3609 (the trigger,
2026-08-07), #3626 (specification validation in CI), #3616
(`base.py` under mypy), #3642 (variadic handlers in the audit),
#3629 (body-supplied `all`, see D6 below), #3615 (`log_request`
discarding headers), #3739 (the ref decorators' undeclared
`namespace`, closed when phase 4 merged 2026-09-09), #3606 (JWT
rejection attribution, PR #3714, merged 2026-08-12), #3371
(`record_exception` tracebacks only at DEBUG) and #3523 (a
partial `cpuinfo` probe raising `KeyError` in `get_user_agent`,
closed when phase 5 landed `dc6019d6a`).

**Filed by phase 4, and deliberately not fixed by it:** #4098 (the
`get_args` fold, see the carried section below) and #4100 (an
explicit `thin: false` on a snapshot request, which two comments
in the tree wrongly blamed on this plan — the compiled path never
injects, so no phase of this plan unblocks it; it needs a client
release which omits the key).

**Filed by phase 5, and deliberately not fixed by it:** #4161 (the
proxy path answering an unreachable node — or the local nodelock
socket refusing a connection — with an unqualified 500, see F9 in
[phase 5](PLAN-api-input-validation-phase-05-narrow.md)). Filing
it required re-verifying the finding first: an unrelated fix
(#3743, merged 2026-08-21) had already closed the largest of the
three classes the phase 5 survey recorded, so the issue is scoped
to the two proxy call sites and the local-socket case that fix
does not cover, not to the survey's original table.

**Filed by phase 7's survey, and deliberately not fixed by it:**
[#4223](https://github.com/shakenfist/shakenfist/issues/4223), a null
object reference resolving to an arbitrary object. The pushed-down
`from_db_by_ref` implementations build
`ObjectFilterCriteria(name=object_ref)`, and a `None` name reads as *no
name filter* rather than as a name of `None`, so the query returns
every active object in the namespace and one match is answered as
though the caller had named it. The only path a caller can reach is the
netdesc's `network_uuid`, which `_netdesc_safety_checks` tests for
presence rather than for a value, and it was measured end to end:
`POST /instances/{ref}/interfaces` with `{"network_uuid": null}`
answers 200 and creates an interface. Phase 7 leaves it alone on
purpose — its networkspec schema will make `network_uuid` a required
string, which closes the reachable path while leaving the lookup
function wrong for the next caller, and that is a mask rather than a
fix. The issue was filed carrying `automated-fix-attempted`, which is
the first time this plan has applied that label at filing rather than
watching the fixer apply it afterwards.

**Closed by phase 6**, all three when
[#4199](https://github.com/shakenfist/shakenfist/pull/4199) merged
on 2026-09-15: #3269 (`ec406a78a`, step 4's commit `Enforce the
formats the API publishes.` — proven by
`test_format_validation.py`'s `test_wrapped_base64_user_data_still_reaches_placement`,
which drives an unencoded `user_data` through a real
`POST /instances` and asserts the config drive decode at
`instance.py:1930` is never reached), #323 (`c7a432886`, step 5's commit
`Refuse an overlapping netblock.` — proven by
`shakenfist/tests/external_api/test_network.py`'s overlap cases and
the cluster CI case in
`shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_networking.py`)
and #4195 (`7cf4d47b3`, the review round's commit `Answer a bad blob
and a bad mode honestly.` — proven by the
`('InstanceAgentPutEndpoint', 'post', 'mode')` row of
`test_required_sweep.py`, which expects the 406 the guard at
`external_api/instance.py:1955` answers instead of the 500 that
`int()` and `symbolic_to_numeric_permissions()` between them used
to raise).

Those three SHAs are the commits as they landed. An earlier
revision of this paragraph named `03ea26514` and `d6b84b365` for
the first two, which is what they were before #4199 was rebased
onto `develop` shortly before it merged; those objects now exist
in no clone but the one that wrote them. A SHA recorded while a
branch is still in flight is a SHA a rebase can invalidate, so
read them off the first-parent range after the merge, the way the
`Merged` column above is built.

**Closed by phase 7, and what closed each:**
[#528](https://github.com/shakenfist/shakenfist/issues/528), the
parent, and since 2026-09-12 also the tracker for what #936
described. Its scope was two things wearing one title. Its own text
described extending `use_kwargs` coverage across the remaining
endpoints, and that was overtaken by **phase 4** rather than by this
phase: phase 3 compiled every declaration in the tree into a schema
and phase 4 turned rejection on, which covered every endpoint without
extending `use_kwargs` to any of them — read literally, #528 was
closed on 2026-09-09 and nobody noticed. What remained was #936's
scope, the element schemas, and that is phase 7. The issue's body was
rewritten to the element-schema scope before it was closed, so that
its history does not read as though the `use_kwargs` work were
abandoned.

**Closes on merge, in our judgement:**
[#3612](https://github.com/shakenfist/shakenfist/issues/3612), the
mechanism issue. It named two things. The broad `except TypeError`
returning interpreter messages was deleted by **phase 5** (D23), and
phase 5 went further and made every 500 answer a bare `server error`
carrying no exception detail at all (D31). Body parameters reaching
handlers untyped was closed at the top level by phases 3 and 4 and at
every nesting depth by phase 7, which is the half that was still open
when this phase started: before it, `arrayofdict` compiled to
`fields.List(fields.Dict())` with no value schema, so a wrong-typed
value one level down was unvalidated in every mode. We think it can
be closed outright rather than rescoped, because nothing of what it
describes survives: the two structures still carrying no element
schema (`metadata`, and the mapping rules' `bound_claims`) are
uninterpreted by design and guarded by hand respectively, which is a
recorded decision (D47) rather than a residual. It is left open until
phase 7 merges rather than closed from a branch.

**Still open and still owned by this plan:** #2094.

**Reduced but not closed by phase 7:**
[#4167](https://github.com/shakenfist/shakenfist/issues/4167).
Its items 2 and 3 — a `disk[].base` set to a truthy non-string, and a
`network[].network_uuid` set to a non-string, both an `AttributeError`
in `shakenfist/util/general.py` and both a recorded 500 — now answer
`400 disk[0].base: Not a valid string.` and
`400 network[0].network_uuid: Not a valid string.`, pinned in
`shakenfist/tests/external_api/test_nested_sweep.py`. Item 1 survives
in part. Phase 6's required-ness enforcement closed its `cpus` and
`memory` cases, which now answer `400 cpus: declared required but not
supplied`; `uefi` and `secure_boot` are `required=False` booleans, so
that enforcement does not reach them, and no element schema contains
them. `POST /instances {"uefi": null}` was re-measured through the
phase 7 sweep fixture at `enforce` during the phase 7 close-out and is
still a recorded 500 answering `server error`. The issue was commented
with that measurement rather than closed.

**Phase 5 was overtaken from outside, as predicted.** Three of
its four attribution issues were picked up by the automated issue
fixer rather than by this plan — #3615, #3606 and #3371, leaving
only #3523. That is fine: they were genuinely independent of
phases 3 and 4, which is why they were grouped rather than
sequenced. It is recorded because it changed what phase 5 *is*.
The forecast written here was that phase 5 would reduce to the
single item which actually depends on phases 3 and 4 — narrowing
`except TypeError` to JWT errors, which cannot happen until a
validation layer is rejecting the malformed input that broad
catch absorbs. That is now the fact rather than the forecast.
Nobody picks that item up incidentally, because on its own it
looks like a regression risk with no visible benefit.

**Phase 6 was overtaken the same way, four hours after its plan
merged.** #534 was in phase 6's scope, named in a merged plan,
and the automated issue fixer landed
[#4183](https://github.com/shakenfist/shakenfist/pull/4183) for
it on 2026-09-12 — a complete fix, wider than the one phase 6
briefed, covering both callers of `_netdesc_safety_checks` with a
drift test and a guest CI case. Phase 6 step 5 is now #323 alone.
Being overtaken is not a failure: the work is done and the issue
is closed. It is recorded because two phases running have lost
scope this way, and because the mitigation is cheap and was not
used — an `automated-fix-attempted` label applied when the issue
is filed reserves it against the fixer while a branch is in
flight.

It then happened a second time inside the same phase, and this
time the two fixes collided. Phase 6's survey filed
[#4194](https://github.com/shakenfist/shakenfist/issues/4194) (a
missing blob answered as a 500) and #4195 seventeen seconds apart
on 2026-09-13, and the branch fixing both was already open. The
fixer opened
[#4196](https://github.com/shakenfist/shakenfist/pull/4196) for
#4194 an hour and three quarters later and it merged at 08:38,
carrying the same one-line change this branch carried. Nothing
broke: rebasing #4199 onto `develop` dropped the now-identical
hunk silently, and the only trace is that the phase's
`instance.py` diff shrank by two lines and that a second test for
the same behaviour lives in `test_agent_operation_parameters.py`.
It is recorded because it shows what the mitigation is worth and
when it has to be applied. #4194 does carry
`automated-fix-attempted` — the fixer adds it when it starts, an
hour and three quarters after the useful moment — while #4195,
seventeen seconds younger and never picked up, carries no label
at all. The label only reserves an issue if it is there before
the fixer looks.

### Carried into phase 2 from phase 1

Phase 1 corrected which location each parameter declares. It did
not change how `swagger_helper()` renders them, which leaves two
specification-validity problems for phase 2 to pick up. Measured
with `openapi_spec_validator` over the flasgger output (raised by
the ninth review round and re-measured independently): develop
produced 363 validation errors and this branch 241 at that
measurement, with the entire "path template variable has no
corresponding path parameter" class eliminated by the location
audit. The `security` fix below then landed in phase 1 after all,
and a rebase brought in the eleven federated-authentication
handlers, so the branch now measures **129**: 128 from the body
class, 1 from `schemes`.

* **Multiple body parameters per operation — fixed in phase 2.**
  Swagger 2.0 permits at most one `in: body` parameter, and it
  must carry a `schema` rather than `type`/`format`;
  `swagger_helper()` emitted `type`/`format` for everything, and
  32 of 132 operations declared more than one body parameter (23
  before phase 1 — correcting `key` from query to body on the
  metadata endpoints added most of the rest, and the
  federated-authentication endpoints arrived with several more).
  Phase 2's second PR collapses an operation's body declarations
  into a single generated `schema` at render time — declarations
  keep their one-tuple-per-parameter shape, which is what the
  audit reads and phase 3 compiles. The raw-body marker renders
  as a binary schema, and declaring raw and named body parameters
  together is rejected at import time. This took the validation
  error count from 128 to zero, so the ratchet in
  `test_openapi_spec.py` became a plain validity assertion.
* **`schemes` renders as a string, not an array — fixed in
  phase 2.** `API_ADVERTISED_HTTP_SCHEMES` is typed `str` in
  `config.py` while its own description calls it a "space
  separated list", and `app.py` fed it straight into the
  specification's top-level `schemes` key, which OpenAPI 2.0
  requires to be an array of strings. A default deployment
  therefore published `schemes: 'http'`. Pre-existing —
  introduced in `01ef8a563`, not by this plan. Phase 2's first PR
  splits the documented space-separated contract at the consumer
  in `app.py`, and added the `securityDefinitions` entry the
  `security` requirements referenced without defining.
* **`security` renders as an object, not an array — fixed in
  phase 1 after all.** `swagger_helper()` emitted
  `'security': {'bearerAuth': []}`, but OpenAPI 2.0 requires
  `security` to be an *array* of requirement objects. This was
  deferred here twice on scoping grounds, and the eleventh review
  round questioned deferring a one-line change inside a function
  the phase was already editing (the `format`/duplicated-key
  fixes were the same class of renderer tweak). It shipped:
  `'security': [{'bearerAuth': []}]` eliminated the entire
  126-error class, the largest remaining. Kept in this list as a
  record of the scoping call rather than as work to do.
* **Nothing validates the generated specification — fixed in
  phase 2.** Phase 1's path-implies-required rule exists to
  satisfy linters and client generators, and was checked by hand.
  Phase 2's first PR added
  `shakenfist/tests/external_api/test_openapi_spec.py`, which
  runs `openapi_spec_validator` over flasgger's output and holds
  the remaining invalidity to an exact ratchet count (128, all in
  the body-parameter class above), failing on any new error class
  or any change in the count
  ([#3626](https://github.com/shakenfist/shakenfist/issues/3626)).
  The test landed before the renderer fix on purpose: it turns
  "invalid in N places" into a number that moves.

### Carried into phase 3 from phase 1

**Generate the derivation's input space rather than sampling it.**
A precondition for compiling, in the same way the audit itself
was.

Phase 1 took five review rounds, and four of them found a defect
in the machinery added by the round before: the Werkzeug
converter regex, the `flask.request.args` fallback, the webargs
scope leak, and an emptied parameter list. Every one was
`declarations.py` misreading source, and every one was a shape
that did not occur anywhere in the tree — so no amount of testing
against the tree could have found them, and neither could
mutating it, which only permutes shapes already present.

While the declarations are documentation, a misread costs a wrong
line in the published API. Once phase 3 compiles them, the same
misread rejects a valid request: a `path` parameter derived as
`body` produces a schema hunting the JSON body for a URL segment,
and decision D6's query-string fallback is granted only to
parameters derived as `query`. The cost of the defect class rises
sharply exactly here, which is why this belongs to phase 3 and
not earlier.

The shape to build is a **combinatorial generator**, not a
fuzzer. Enumerate the axes and assert the derivation recovers
what the source was constructed to mean:

| Axis | Values |
|---|---|
| Route | absent, `<x>`, `<path:x>`, `<int(min=1):x>`, non-literal |
| webargs | none, `get_args` on the class, on the module, inline dict, `location='json'` |
| `request.args` | absent, `.get()`, subscript, on a non-request object |
| Declaration | well-formed, wrong arity, non-literal name, raw-body sentinel |

A few dozen cases, deterministic, well under a second. The oracle
is free because the source is constructed knowing where the
parameter comes from, which is what makes this different from
mutating declarations in the tree: `audit()` derives the truth
and compares, so flipping a declared location and asserting drift
tests the comparison, not the derivation. Every real defect was
on the other side of that comparison.

No new dependency. `hypothesis` is not in the project, and
randomness buys nothing over enumerating a space this small —
the value is in covering the axes, not in sampling them.

`tools/check-api-declaration-guards.sh` stays as it is. It
mutates the real tree to prove the *guards* fire, which is a
different question from whether the *derivation* is right, and
the two are complementary.

**D6's query-string fallback shipped early, at three sites.**
[Issue #3629](https://github.com/shakenfist/shakenfist/issues/3629):
`all` on the outstanding-operations endpoints was bound with
`@use_kwargs(get_args, location='query')`, and webargs finishes
with `kwargs.update(parsed_args)`, so the `load_default=False`
from an absent query string overwrote the `all=True` that
`log_request` merged in from the JSON body. The shipped client
only ever sends a body, so the parameter never worked through
it.

This was predicted here to close with phase 3, "or sooner". It
closed sooner, in `0de6c3b5c` (2026-08-09), and phase 3 inherits
the mechanism rather than choosing one:

* `base.py` registers a **`json_or_query` webargs location
  loader** which merges `req.args` and the JSON body with the
  body authoritative, then drops keys the schema does not name
  (mirroring webargs' `unknown=EXCLUDE` default for the query
  location). The instance, artifact and network
  outstanding-operations endpoints are bound to it.
* **A `('query', 'json')` tuple location was tried and
  rejected.** webargs keys validation failures by location, and
  a tuple key is not JSON-serialisable, so a 422 becomes a 500.
  Phase 3 must not re-derive this: the custom loader is the
  supported shape.
* `declarations.py` derives a schema bound to `json_or_query`
  as a **`query`** location, so the published declaration is
  unchanged and phase 1's audit still holds.

Phase 3 therefore generalises an existing, tested loader to
every parameter derived `query`, rather than introducing a
second precedence rule alongside it. See
[phase 3](PLAN-api-input-validation-phase-03-compile-and-warn.md).

### Carried into phase 4 from phase 3

The measurement window found two declaration bugs, and phase 4
fixes both before it enforces. Their detail is in
[phase 4](PLAN-api-input-validation-phase-04-enforce.md); what
belongs here is the one item phase 4 declined.

* **The `get_args` fold is deferred, and this plan's description
  of it was wrong.** Phase 4's line above used to read "fold the
  hand-authored `get_args` schemas into the compiled path".
  Read as "delete the four `@use_kwargs` decorators", that is a
  defect rather than a refactor: `validation.check()` returns
  findings and `validate_request` calls through, so the compiled
  path never coerces or injects anything, and `log_request`
  merges only the JSON body -- nothing merges
  `flask.request.args`. `@use_kwargs` is therefore the sole
  mechanism by which a query-string parameter reaches a handler.
  Removing it from `blob.py` would silently revert `offset` and
  `limit` to their signature defaults, reading a whole blob where
  the caller asked for a range.

  The defensible reading is to keep `@use_kwargs` and generate
  its schema from the same declaration list `swagger_helper()`
  receives, which removes the second source of truth D2 objected
  to. That needs signature defaults at runtime and moves
  `blob.py`'s hand-rolled negative-offset check into a field,
  changing an error message -- a second request-visible change in
  the phase that flips enforcement. It is tracked as
  [#4098](https://github.com/shakenfist/shakenfist/issues/4098)
  and remains a candidate for a later phase. Phase 6 did not
  take it: that phase corrects declarations and enforces them,
  and deriving the schema from signature defaults is a change to
  where declarations come from. Enforcement does not depend on
  it: the compiled check runs first, so the duplication is
  inert.

## Open questions for phase 0

All answered above; retained as the record of what phase 0
was asked to decide.

1. **webargs/marshmallow, pydantic, or hand-rolled?** Pydantic is
   already a core dependency (config and `schema/`), but webargs
   is what the four existing sites use and is already wired into
   Flask request parsing. Which one, and is consistency with
   `schema/` worth more than consistency with the existing four
   endpoints?
2. **Compile the existing declarations, or author new schemas?**
   Compiling gets 96% coverage for free but inherits any drift.
   Authoring is honest but is 254 hand-written declarations.
   Is there a middle path — compile, then let an endpoint
   override with an explicit schema where the declaration is
   insufficient?
3. **Where does validation sit in `method_decorators`?** It must
   run after authentication (so an unauthenticated caller cannot
   probe the schema) but before the handler. `CLAUDE.md` warns
   that this ordering is subtle; what breaks?
4. **What is the malformed-input response shape?** A single
   `400 {"error": "..."}` naming the parameter, or a
   field-keyed structure? Does anything depend on the current
   text? What does `shakenfist-client` do with it?
5. **How long does warn-only run, and what makes it "quiet
   enough"?** sfcbr is one cluster with one workload; CI is
   another. Is that enough evidence to enforce?
6. **What happens to a query parameter on a GET?** The events
   endpoints read `limit` only from the JSON body — a `?limit=5`
   query string is silently ignored, which is surprising and
   fragile (a GET body is not guaranteed to survive proxies).
   Should the migration accept `location=('query', 'json')` for
   these, and is that a breaking change for anyone?
7. **Do we validate responses too?** Out of scope as written, but
   the same declarations describe response examples, and
   `external_view()` drift is a real source of client bugs.
8. **Body keys silently overwriting path parameters.** Is that
   ever intentional? `ArtifactVersionEndpoint.delete` currently
   lets a body `version_id` override the URL segment. If not
   intentional, the schema layer is where it stops.

## Non-goals

* Rewriting the API surface, changing resource paths, or
  versioning the API.
* Response-shape validation (see open question 7).
* Authentication or authorization changes.
* The mariadb-layer error semantics in #3373.
