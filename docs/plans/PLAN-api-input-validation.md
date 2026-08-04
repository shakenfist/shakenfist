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

**Status: phases 0 and 1 complete.** The open questions at the
bottom are answered in the Decisions section; see
[`PLAN-api-input-validation-phase-00-decisions.md`](PLAN-api-input-validation-phase-00-decisions.md)
for the measurements behind them and
[`PLAN-api-input-validation-phase-01-declaration-audit.md`](PLAN-api-input-validation-phase-01-declaration-audit.md)
for what the audit found. Phases 2 onward are not yet cut into
per-phase files.

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
job at four sites (`blob.py:187`, `network.py:796`,
`artifact.py:871`, `instance.py:1784`) — all of them
`location='query'`, none on request bodies.

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
| 2: Type vocabulary | Not started | New tokens and the constraints element, rendered into the OpenAPI so the bounds are visible to callers rather than invisible the way the events `limit` cap was. Also: collapse the N body parameters of an operation into one `body` parameter carrying a generated `schema`, and add a test that validates the generated specification — see below |
| 3: Compile and warn | Not started | Declarations to schemas; validate in warn-only mode; deploy to sfcbr and read the logs |
| 4: Enforce | Not started | Turn on rejection once the warn log is quiet, with one malformed-input response shape that never contains interpreter text; fold the four hand-authored `get_args` schemas into the compiled path |
| 5: Narrow the handlers | Not started | Narrow `except TypeError` to JWT errors; fix the attribution issues (#3523, #3371, #3606, #3615) |
| 6: Required and semantics | Not started | Enforce `required` — or decide not to, since it is the change most likely to break working clients; semantic validators for #534, #3269, #323, #936 |


### Carried into phase 2 from phase 1

Phase 1 corrected which location each parameter declares. It did
not change how `swagger_helper()` renders them, which leaves two
specification-validity problems for phase 2 to pick up:

* **Multiple body parameters per operation.** Swagger 2.0 permits
  at most one `in: body` parameter, and it must carry a `schema`
  rather than `type`/`format`; `swagger_helper()` emits
  `type`/`format` for everything. 29 operations declare more than
  one body parameter (23 before phase 1 — correcting `key` from
  query to body on the metadata endpoints added most of the
  rest). Every individual declaration is now right and the
  specification is still invalid, so the generated-client problem
  this plan opens with is only partly closed. The fix is to
  collapse an operation's body parameters into a single `body`
  parameter with a generated `schema` object, which is a change to
  the renderer rather than to any declaration.
* **`schemes` renders as a string, not an array.**
  `API_ADVERTISED_HTTP_SCHEMES` is typed `str` in `config.py` while
  its own description calls it a "space separated list", and
  `app.py` feeds it straight into the specification's top-level
  `schemes` key, which OpenAPI 2.0 requires to be an array of
  strings. A default deployment therefore publishes
  `schemes: 'http'`, and a two-scheme one publishes a single
  nonsense string rather than two entries. Pre-existing —
  introduced in `01ef8a563`, not by this plan — but it is the
  *first* thing a validator trips over, ahead of the body
  parameters above, so #3626 has to fix or explicitly waive it to
  reach anything else.
* **Nothing validates the generated specification.** Phase 1's
  path-implies-required rule exists to satisfy linters and client
  generators, and was checked by hand. A unit test running
  `openapi_spec_validator` over flasgger's output would catch the
  next regression, including the body-parameter one above. The
  only current functional coverage fetches `swagger-ui.css`.
  Filed as [#3626](https://github.com/shakenfist/shakenfist/issues/3626)
  so it is not gated on the renderer work — the test is worth
  having before the fix, since it turns "invalid in N places"
  into a number that moves.

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
