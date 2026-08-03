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

**Status: draft.** Phase 0 (research and decisions) has not run.
The open questions at the bottom are the input to it.

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
ever checked them. There is direct evidence of drift in the tree
right now:

* `artifact.py:742` declares a location of `'qeury'` — a typo.
* `artifact.py:641` declares a location of `'post'`, which is not
  an OpenAPI parameter location at all.

Both are silently ignored today and would become enforcement
bugs. `required` is likely worse: `mode` on
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

## Shape of the work

Sketch only. Phase 0 decides the real structure; this exists so
the open questions have something to argue about.

1. **Audit and normalise the declarations.** Fix the two known
   typos, reconcile every declared type and location against what
   the handler actually reads, and make `swagger_helper()` reject
   an unknown location the way it already rejects an unknown type.
   This is mechanical and can land immediately — it is a docs
   correctness fix on its own merits.
2. **Extend the type vocabulary** to cover unsigned and bounded
   integers, and format-constrained strings, keeping the
   generated OpenAPI honest about the new constraints.
3. **Compile declarations to a schema** and validate in
   **warn-only** mode: log the endpoint, parameter, declared
   type and offending value that *would* have been rejected.
   Deploy to sfcbr. Read the logs.
4. **Enforce types**, once the warn-only logs are quiet, with a
   single consistent malformed-input response shape that never
   contains interpreter text.
5. **Narrow the exception handlers.** With inputs typed,
   `handle_authorization_exceptions` can be narrowed to the JWT
   errors it was written for, and the attribution issues
   (#3523, #3371, #3606) can be fixed against a chain that no
   longer sees client-input errors at all.
6. **Enforce `required`,** or decide not to. Separate and later,
   because it is the change most likely to break working clients.
7. **Structured and semantic validators** for the specs in #936,
   and the semantic checks in #323, #534 and #3269.

## Open questions for phase 0

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
