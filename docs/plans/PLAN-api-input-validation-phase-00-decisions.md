# Phase 0: Research and decisions for API input validation

## Context

This is phase 0 of
[`PLAN-api-input-validation.md`](PLAN-api-input-validation.md).
It is a **decisions phase: no production code changes.** Its
output is documentation — the measurements below, and a
"Decisions" section on the master plan that turns its eight open
questions into committed answers the later phases implement
against.

The master plan rests on one hypothesis: that the
`swagger_helper()` parameter declarations carried by 124 of our
129 handler methods are accurate enough to compile into a
validation schema, rather than being documentation that has
drifted from the code over six years. That hypothesis is either
true, in which case this plan is cheap, or false, in which case
it is a 254-parameter authoring exercise. **Measuring it is the
central task of this phase**, and everything else follows from
the answer.

**Status: complete.** The measurements are recorded below and
the decisions are on the master plan.

## Key references in the existing code

- `shakenfist/external_api/base.py` — `swagger_helper()` and its
  `argtypes` table (`base.py:141-160`); `log_request` and the
  body-to-kwargs merge (`base.py:588-598`);
  `handle_authorization_exceptions` and its broad
  `except TypeError` (`base.py:663-682`);
  `Resource.method_decorators` (`base.py:911-918`).
- The four existing `use_kwargs` sites: `blob.py:170-187`,
  `network.py:781-796`, `artifact.py:846-871`,
  `instance.py:1767-1784`.
- `shakenfist_utilities.api.error()` — the single error-response
  constructor, producing `{'error': ..., 'status': ...}`.
- `shakenfist_client/apiclient.py` in the sibling `client-python`
  repo — `_actual_request_url()` (how parameters are actually
  transmitted) and `APIException` (how errors are surfaced).

## Measurements

All figures produced by AST analysis of
`shakenfist/external_api/*.py` at `b844fd98e`.

### Coverage

| Measure | Value |
|---|---|
| Handler methods (`get`/`post`/`put`/`delete`/`patch`) | 129 across 20 files |
| Carrying a `swag_from(swagger_helper(...))` declaration | 124 (96%) |
| Declared parameters in total | 254 |
| Declared `body` / `query` / `path` | 113 / 118 / 3 |
| Handlers using webargs `use_kwargs` | 4, all `location='query'` |

Declared type tokens in use: `string` 94, `uuidorname` 53,
`uuid` 27, `boolean` 13, `integer` 13, `node` 11, `namespace` 10,
`url` 5, `arrayofdict` 3, `number` 2, `dict` 2, `arrayofstring` 1,
`ipv4` 1, `binary` 1.

### Accuracy — the hypothesis under test

Comparing each declaration against the kwargs its handler
actually accepts:

| Measure | Value |
|---|---|
| Declared names matching a real handler kwarg | **229 of 236 (97%)** |
| Declared but not accepted | 7 |
| Accepted but not declared | 20 |
| Declared type contradicting the signature default | **0** |

The seven name mismatches are all straightforward drift, and
each is a live documentation bug:

| Endpoint | Declared | Actually accepted |
|---|---|---|
| `InstancesEndpoint.post` | `sshkey` | `ssh_key` |
| `InstancesEndpoint.post` | `userdata` | `user_data` |
| `NetworkEndpoint.get` / `.delete` | `artifact_ref` | `network_ref` |
| `NodeEndpoint.get` / `.delete` | `node_name` | `node` |
| `UploadDataEndpoint.post` | `binary data` | (raw body, not a kwarg) |

The first two are the notable ones: the published OpenAPI has
been telling callers to send `sshkey` and `userdata` when
instance creation reads `ssh_key` and `user_data`.

The 20 accepted-but-undeclared are mostly decorator-injected
objects (`*_from_db`), plus genuine documentation gaps: `all` on
the four `clusteroperations` endpoints, `value` on the metadata
delete endpoints, `max_versions` on `LabelEndpoint.post`, and
`external_namespace` on `AuthNamespaceTrustsEndpoint.post`.

Two declarations name a location that does not exist:
`artifact.py:641` says `'post'` and `artifact.py:742` says
`'qeury'`. `swagger_helper()` validates the *type* token via a
dict lookup but never validates the location, so both are
silently ignored today.

### The four hand-authored schemas already drift

`BlobDataEndpoint` declares `offset` and `limit` **twice** —
once as marshmallow fields in `get_args`, once in
`swagger_helper` — and the other three declare `all` in
`get_args` while omitting it from `swagger_helper` entirely.
The endpoints that do validation "properly" today are the
endpoints whose documentation is wrong, because there is no
single source of truth.

### Client transmission behaviour

`_actual_request_url()` in `shakenfist_client` serialises
`data` to a JSON body for **every** method, including `GET`,
and never builds a query string. The official client therefore
sends nothing in the query string today, and a query-string
fallback cannot break it.

`APIException` stores `status_code` and the raw response `text`
and never parses the message. Across our own unit and CI tests,
11 assertions touch API error text.

### Dependencies

`webargs==8.7.1`, `marshmallow==4.3.0` and `pydantic==2.13.4`
are all already pinned direct dependencies. The library choice
is a fit question, not a dependency question.

### Decorator ordering

Verified empirically against the installed `flask_restful`:
entries in `method_decorators` are applied so that the **last
entry is outermost and runs first**, and the **first entry is
innermost and runs last**, immediately before the handler's own
per-method decorators.

```
execution order: LAST-in-list -> second -> FIRST-in-list -> handler
```

So the current chain executes as
`suppress_exceptions_to_client` → `record_exception` →
`handle_database_unavailable` → `handle_authorization_exceptions`
→ `log_request` → `_authenticate_unless_public` → per-method
decorators → handler.

## Decision items

Each maps to an open question on the master plan. The resolved
form of each is on the master plan under "Decisions"; the
reasoning is here.

### D1 — Validation library (open question 1)

**webargs + marshmallow.** All three candidates are already
pinned, so this is decided on fit: webargs exists specifically
to parse and validate Flask request arguments, is already wired
into four endpoints, and supports per-location parsing —
which is what open question 6 needs. Pydantic in this codebase
models *persisted state* (`shakenfist/schema/`, config); using
it for wire input would blur a boundary that is currently
clean, and would need its own Flask integration written.

### D2 — Compile the declarations (open question 2)

**Compile, with a per-endpoint override escape hatch.** The
hypothesis held: 97% of declared names match a real kwarg and
zero declared types contradict a signature default. Authoring
254 declarations by hand to replace declarations that are
already correct would be make-work, and the four endpoints that
*do* hand-author schemas are precisely the ones whose
documentation has drifted — two sources of truth is the disease,
not the cure.

The 27 mismatches are the audit backlog for phase 1, not an
argument against compiling.

### D3 — Placement in the decorator chain (open question 3)

**Insert at index 0 of `method_decorators`.** Given the verified
ordering, index 0 is innermost, so validation runs *after*
`_authenticate_unless_public` (an unauthenticated caller cannot
probe the schema) and *before* every per-method decorator
(`arg_is_artifact_ref` and friends do database lookups with
these values, so they must not see unvalidated input).

webargs' default error handling raises a 422 through Flask's
error handling, which would bypass `sf_api.error`. The
integration must install its own error handler so that
validation failures are emitted through the same path as every
other API error.

### D4 — Error response shape (open question 4)

**Keep the existing shape**, `sf_api.error(400, ...)` producing
`{'error': ..., 'status': ...}`, with the message formatted as
`"<parameter>: <reason>"`. No structured field-keyed body.

The official client never parses the message — it stores the
raw text on the exception and surfaces it to humans — and only
11 assertions in our own tests touch error text, so the cost of
changing message wording is bounded and known. A new body shape
would be a larger compatibility surface for no demonstrated
consumer.

### D5 — Warn-only exit criterion (open question 5)

**Warn-only until every remaining rejection is one we intend.**
Not a fixed duration: the criterion is that the warn log has
been read and every distinct (endpoint, parameter, reason)
signature in it is either a declaration bug we then fix, or
input we are content to start rejecting.

The window must cover at least one full functional CI run (which
exercises the API far more broadly than sfcbr's steady state)
plus seven days of sfcbr, whose logs already ship to Loki and
can be queried per signature. Warn records must carry endpoint,
parameter, declared type, and the offending value's *type* —
not its value, which may be user data.

### D6 — Query-string fallback (open question 6)

**Accept `location=('json', 'query')` for parameters declared
`query`, keeping the JSON body authoritative.** Purely additive:
the official client sends everything in the body regardless of
method, so nothing that exists today can break. It removes the
GET-with-a-body fragility for anyone using curl or a browser,
and it makes the OpenAPI honest, where today a `query`
declaration is a documented lie.

### D7 — Response validation (open question 7)

**Out of scope, and not deferred to a later phase of this
plan.** It has a different risk profile — a wrong response
schema breaks working clients at runtime, where a wrong request
schema only rejects a request — and no reported issue asks for
it. Revisit as its own plan if `external_view()` drift becomes
a real source of bugs.

### D8 — Body keys overriding path parameters (open question 8)

**Reject a body key that collides with a path parameter,** with
a 400 naming it. Nothing relies on the override, and
`log_request` already special-cases a body `uuid` to
`passed_uuid` (`base.py:593-594`) specifically to dodge one
instance of this collision — evidence that it is a known hazard
rather than a feature.

One documented exception must keep working: `arg_is_artifact_ref`
accepts an `artifact_uuid` supplied in the body by internal
flows (`artifact.py:66-72`). That is not a path parameter on any
mounted route, so it is unaffected.

### D9 — Type vocabulary extension (master plan "Type vocabulary gaps")

**Extend the type token list, and add an optional sixth tuple
element for constraints.** Tokens alone cannot express the
bounds the reported issues need. Minimum additions:

| Need | Mechanism | Issue |
|---|---|---|
| Non-negative integers (artifact version index, `max_versions`) | `unsignedinteger` token | #3609 fallout |
| Bounded integers (events `limit`, pagination) | `{'minimum': n, 'maximum': m}` constraint | #3609, #1974 |
| MAC address format | `macaddr` token | #534 |
| Base64-encoded payloads | `base64` token | #3269 |
| Netblocks excluding reserved ranges | `netblock` token + semantic validator | #323 |

`minimum`, `maximum` and `pattern` are all valid Swagger 2.0
parameter keywords, so the constraints render into the published
OpenAPI rather than living only in code — which is the property
that made the events `limit` cap invisible to callers.

Semantic checks that need cluster state (does this netblock
overlap the *configured* floating network?) are validators
registered against a token, not part of the token itself.

## Success criteria

- [x] Declaration accuracy measured, not assumed.
- [x] Library, placement, response shape and scope decided with
      the reasoning recorded.
- [x] Decorator ordering verified empirically rather than from
      the code comment alone.
- [x] Client transmission and error-handling behaviour confirmed
      against `client-python`, not assumed.
- [x] Decisions section on the master plan; phase table and
      `docs/plans/index.md` updated.
- [x] No production code changed.

## What phase 1 inherits

The audit backlog, ready to execute:

1. Two invalid locations (`'post'`, `'qeury'`), and a
   `swagger_helper()` that should reject an unknown location the
   way it already rejects an unknown type.
2. Five wrong parameter names, two of which (`sshkey`,
   `userdata`) are wrong in the published OpenAPI for instance
   creation.
3. Twenty undeclared-but-accepted parameters to either declare
   or deliberately hide.
4. Four hand-authored `get_args` schemas to fold into the
   compiled path so they stop being a second source of truth.
