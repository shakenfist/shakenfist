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

**Status: phases 0, 1, 2 and 3 planned; 0, 1 and 2 complete.**
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
for the phase now ready to start. Phases 4 onward are not yet cut
into per-phase files.

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
* **No format-constrained string.** MAC addresses (#534),
  base64-encoded user data (#3269), netblocks that must not
  overlap reserved ranges (#323).
* **No structured value types.** Disk, network and video specs
  are validated imperatively today (#936).

## Reported issues this plan addresses

Found by scanning all 83 open issues. The core group is the
reason for the plan; the error-contract group is the other half
of "what does a caller see when they get it wrong", and it shares
the same decorator chain.

### Core: declarative validation

| Issue | Filed | Summary |
|-------|-------|---------|
| [#528](https://github.com/shakenfist/shakenfist/issues/528) | 2020-11-11 | **Broaden declarative type/validity checking across all API endpoints.** The parent issue; explicitly notes only ~4 endpoint files use `use_kwargs` today. |
| [#936](https://github.com/shakenfist/shakenfist/issues/936) | 2021-09-02 | Replace hand-rolled instance-create validation with a declarative schema. Video/disk/network specs are validated imperatively in `instance.py`. |
| [#3612](https://github.com/shakenfist/shakenfist/issues/3612) | 2026-08-03 | Body parameters merged into handler kwargs untyped; broad `except TypeError` returns interpreter messages. The mechanism description above. |
| [#3609](https://github.com/shakenfist/shakenfist/issues/3609) | 2026-08-02 | `GET /nodes/<node>/events` with a string `limit` returns a 400 containing a Python type error. The trigger. |
| [#534](https://github.com/shakenfist/shakenfist/issues/534) | 2020-11-12 | Validate MAC address *format* on interface create (uniqueness is already enforced). |
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

| Phase | Status | Description |
|-------|--------|-------------|
| 0: Research and decisions | Complete | Measured declaration accuracy; chose webargs, compilation, chain placement, error shape, warn-only criterion. See [phase 0](PLAN-api-input-validation-phase-00-decisions.md) |
| 1: Declaration audit | Complete | Correct 116 path-parameter locations from the route table, 2 invalid location tokens, 5 wrong names (incl. `sshkey`/`userdata` in the published OpenAPI) and 20 undeclared parameters; make `swagger_helper()` reject unknown locations; add a test that keeps declarations honest. A precondition for phase 3, and a documentation-correctness fix worth landing on its own merits. See [phase 1](PLAN-api-input-validation-phase-01-declaration-audit.md) |
| 2: Type vocabulary | Complete | The specification-validation test (#3626) plus `schemes`/`securityDefinitions` template fixes; one schema-carrying body parameter per operation, taking the validation error count from 129 to zero; `unsignedinteger`/`macaddr`/`base64`/`netblock` tokens and the optional constraints element, rendered into the published OpenAPI so bounds like the events `limit` cap are visible to callers. See [phase 2](PLAN-api-input-validation-phase-02-type-vocabulary.md) |
| 3: Compile and warn | In progress | Code landed 2026-08-13 via #3726: declarations compiled to schemas, warn-only validation ahead of the handlers; four further decisions (D10-D13) recorded in the phase plan, including that an undeclared body key is *already* a 400 carrying interpreter text. The measurement window opened the same day — sfcbr deployed, apparatus hand-verified, a full functional CI run covered — and exits when every finding is explained, no earlier than 2026-08-20. See [phase 3](PLAN-api-input-validation-phase-03-compile-and-warn.md) |
| 4: Enforce | Not started | Turn on rejection once the warn log is quiet, with one malformed-input response shape that never contains interpreter text; fold the hand-authored `get_args` schemas into the compiled path |
| 5: Narrow the handlers | Not started | Narrow `except TypeError` to JWT errors — still owned by this plan, and still gated on phase 4. The attribution issues are being closed independently: #3615 landed 2026-08-10, #3606 is in flight as PR #3714, leaving #3523 and #3371. See the note below |
| 6: Required and semantics | Not started | Enforce `required` — or decide not to, since it is the change most likely to break working clients; semantic validators for #534, #3269, #323, #936 |

### Where the tracked issues stand

Recorded here rather than by editing the tables above, so there
is one place to maintain and the tables stay a record of what
the plan was scoped against.

**Closed since the plan was written:** #3609 (the trigger,
2026-08-07), #3626 (specification validation in CI), #3616
(`base.py` under mypy), #3642 (variadic handlers in the audit),
#3629 (body-supplied `all`, see D6 below), #3615 (`log_request`
discarding headers).

**Still open and still owned by this plan:** #528 (parent), #3612
(the mechanism), #936, #534, #3269, #323, #3523, #3371, #2094;
#3606 is in flight as PR #3714.

**Phase 5 is being overtaken from outside.** Two of its four
attribution issues have been picked up by the automated issue
fixer rather than by this plan. That is fine — they are genuinely
independent of phases 3 and 4, which is why they were grouped
rather than sequenced. It is recorded because it changes what
phase 5 *is*: by the time phases 3 and 4 land, phase 5 is likely
to be the single item that actually depends on them — narrowing
`except TypeError` to JWT errors, which cannot happen until a
validation layer is rejecting the malformed input that broad
catch currently absorbs. Nobody picks that up incidentally,
because on its own it looks like a regression risk with no
visible benefit.

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
