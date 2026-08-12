# Phase 3: Compile the declarations, and warn

Phase 3 of [`PLAN-api-input-validation.md`](PLAN-api-input-validation.md),
following [phase 2](PLAN-api-input-validation-phase-02-type-vocabulary.md).

It turns 271 parameter declarations into marshmallow schemas, runs
them against every request, and **rejects nothing**. The output of
the phase is not a behaviour change; it is evidence about whether
the declarations can safely become one in phase 4.

## Context

Phases 1 and 2 made the declarations correct and made them render
into a valid specification. Nothing yet reads them at request time.

The baseline, measured on `057e24c1a` rather than carried over from
phase 0:

| | Phase 0 | Now |
|---|---|---|
| Handler methods | 129 | **135** |
| Carrying a declaration | 124 (96%) | **132 (98%)** |
| Declared parameters | 254 | **271** |
| ...`path` / `body` / `query` | 3 / 113 / 118 | **136 / 128 / 7** |
| Declarations carrying constraints | n/a | **5** |
| `audit()` drift / underivable / problems | 116 / — / — | **0 / 0 / 0** |

The location column is the phase 1 result: `query` was never right
for 116 of those parameters, and only 7 parameters in the whole API
genuinely arrive in a query string. That matters below, because D6's
query-string fallback turns out to be a seven-parameter feature
rather than a general one.

Type tokens in use, which is what has to be compiled:

```
string 109   uuidorname 55   uuid 26   boolean 19   namespace 13
node 11      unsignedinteger 9   integer 6   url 5   dict 5
arrayofstring 3   number 2   arrayofdict 2   base64 1   netblock 1
ipv4 1
```

## What this phase inherits rather than decides

Three things landed outside the phase and are now constraints on it.

**The query fallback exists.** `0de6c3b5c` registered a
`json_or_query` webargs location loader in `base.py`: query string
and JSON body merged, body authoritative, keys the schema does not
name dropped. Three endpoints are bound to it. Phase 3 generalises
that loader; it does not choose a precedence rule. It must also not
retry the `('query', 'json')` tuple location — webargs keys
validation failures by location, a tuple key is not
JSON-serialisable, and the 422 becomes a 500. That was already
tried.

**`declarations.py` derives `json_or_query` as `query`,** so
published declarations are unchanged and phase 1's audit still
holds.

**`base.py` is under mypy** (#3616), and the pre-commit hook matches
it. The validation layer lands in a type-checked file, which is what
that issue was for.

## What phase 3 must decide that phase 0 did not

Phase 0 answered nine questions. Reading the code for this plan
turned up four more, three of which are only visible from the
runtime side and so could not have been seen when the plan was
written against the declarations.

### D10. What happens to a body key nothing declares

**This is the central decision of the phase, and today's behaviour
is not what the master plan assumes.**

Verified against the running app rather than reasoned about, on a
declared endpoint (`POST /auth`, which is `@public` and so reachable
without a token) with the namespace lookup mocked so the request
reaches the handler:

```
{"namespace": .., "key": ..}                -> 401 unauthorized
{"namespace": .., "key": .., "zzz": 1}      -> 400 {"error": "AuthEndpoint.post()
                                                got an unexpected keyword
                                                argument 'zzz'", "status": 400}
{"namespace": .., "key": .., "uuid": "x"}   -> 400 {"error": "AuthEndpoint.post()
                                                got an unexpected keyword
                                                argument 'passed_uuid'", ...}
```

`log_request` merges every body key into `kwargs` and calls onward.
No handler in the tree is variadic (#3642 guarantees it and the
audit enforces it), so **an undeclared body key on a request that
reaches its handler is already a 400 carrying interpreter text.**
That is #3612 in its most general form, and it is a de-facto
`unknown=RAISE` policy with the worst possible message.

**But "reaches its handler" is load-bearing, and it is what makes
this decision harder than it first looks.** The same probe without
the mock returns `404 namespace not found`, undeclared key and all:
`arg_is_namespace` is a *per-method* decorator, so it runs after
index 0 but before the handler, and its early return means the
`TypeError` never happens. Today an undeclared key is only fatal if
nothing short-circuits ahead of it.

Validation at index 0 runs before every per-method decorator, so it
preempts all of them. That gives:

* **`unknown=EXCLUDE`** (webargs' default for query) drops the key.
  Nothing that succeeds today fails, and nothing that
  short-circuits today changes. Requests that are 400s today would
  start working — a compatibility improvement in the "was broken,
  now works" direction, at the cost of silently swallowing a typo:
  `{"nmae": "x"}` would create something unnamed rather than
  erroring.
* **`unknown=RAISE`** keeps rejecting, with
  `{"error": "zzz: unknown parameter", "status": 400}` instead of
  interpreter text. Requests that 400 today still 400. But a request
  that today is short-circuited to a 404 or 403 by a per-method
  decorator would now be a 400 instead.

So **neither option is behaviour-preserving**, and the earlier
framing of RAISE as "changes no outcome, only the message" was
wrong: it changes the status code on every path where a per-method
decorator currently answers first.

**Recommendation: `RAISE`, provisionally.** A malformed request
being reported as malformed rather than as "namespace not found" is
the more truthful answer, and it keeps the phase's rejections a
superset of today's rather than a different set. But this is exactly
what warn-only exists to settle, so the phase must count the two
populations separately:

* undeclared key on a request that would have reached its handler
  (RAISE and today agree);
* undeclared key on a request something else would have answered
  first (RAISE changes the status).

If the second population is large or is dominated by the official
client, EXCLUDE wins and the recommendation should flip. That
decision belongs to phase 4, with the counts in hand.

**This preemption is general, not specific to unknown keys.** Any
validation failure at index 0 answers ahead of the per-method
decorators, so a request that is both malformed *and* refers to a
nonexistent object moves from 404 to 400 at enforcement. That is
defensible — the request was malformed — but it is a contract change
across the whole API, and phase 4 should state it in the release
notes rather than discover it in a bug report.

### D11. The `uuid` → `passed_uuid` remap is dead, and D8 rests on it

Decision D8 says a body key colliding with a path parameter is
rejected, and cites `log_request` mapping body `uuid` to
`passed_uuid` as evidence "it is a known hazard rather than a
feature".

`passed_uuid` appears exactly once in the tree — at the assignment
in `log_request` itself. No handler accepts it; no declaration names
it. The remap therefore does not dodge the collision. It converts
`{"uuid": ...}` on *every* endpoint into a guaranteed 400 with
interpreter text, as the probe above shows.

Phase 3 should delete the special case and let `uuid` fall under
D10 with every other undeclared key. D8's underlying point survives
— a body key overwriting a path parameter is a real hazard — but its
supporting evidence does not, and the plan should stop citing it.

### D12. Collision detection cannot live in the validator

D3 places validation at index 0 of `Resource.method_decorators`.
That is correct and unchanged: flask_restful applies the list in
order with each wrapping the previous, so index 0 is *innermost*,
running after authentication and still outside every per-method
decorator.

But `log_request` is at index 1, which means it runs **before**
index 0 — and it has already merged the body into `kwargs` by the
time the validator sees anything. A body key that overwrote a path
parameter is, at index 0, indistinguishable from a path parameter
that simply had that value.

So D8 cannot be implemented in the validator. Either `log_request`
records what it overwrote (a request-scoped marker on `flask.g`,
the pattern `record_exception` already uses for
`_RECORDED_EXCEPTION_FIELDS`), or the check lives in `log_request`
itself. The marker is preferable: it keeps `log_request` doing one
job and keeps the policy in one place.

### D13. How the compiled schema reaches the handler

`swagger_helper(section, description, parameters, responses, ...)`
does not know which class or method it is decorating, so it cannot
key a registry by handler. And `swag_from`'s `specs_dict` is an
attribute on the function: `base.py` warns in two places that
several of its decorators predate `functools.wraps` and do not
propagate attributes, which is why `_sf_public` is documented as
"apply it as the outermost decorator" with a structural test to
enforce it. Reading `specs_dict` off a wrapped bound method is the
same trap.

**Build the registry at mount time in `app.py`**, from the 94
`api.add_resource()` calls. That is the same ground truth phase 1
used for the location audit, it yields the class, the route and
therefore the path-parameter set together, and it involves no
attribute propagation. The validator looks up
`(type(self).__name__, request.method.lower())`.

A test asserts every mounted handler resolves to a compiled schema,
so a route added without one fails rather than silently skipping
validation. That is the same "absence must not be indistinguishable
from success" rule the audit was rewritten around.

## Shape of the work: three PRs and a measurement

### PR 1 — Generate the derivation's input space

The precondition the master plan sets, and the reason it is a
precondition rather than a step: while declarations are
documentation, a misread costs a wrong line in the published API;
once they are compiled, the same misread rejects a valid request.

Phase 1 took five review rounds and four of them found a defect in
the machinery added by the round before — the Werkzeug converter
regex, the `flask.request.args` fallback, the webargs scope leak, an
emptied parameter list. Every one was `declarations.py` misreading
source, and every one was a shape absent from the tree, so neither
testing against the tree nor mutating it could have found them.

Enumerate the axes and assert the derivation recovers what the
source was constructed to mean:

| Axis | Values |
|---|---|
| Route | absent, `<x>`, `<path:x>`, `<int(min=1):x>`, non-literal |
| webargs | none, `get_args` on the class, on the module, inline dict, `location='json'`, `location='json_or_query'` |
| `request.args` | absent, `.get()`, subscript, on a non-request object |
| Declaration | well-formed, wrong arity, non-literal name, raw-body sentinel |

The `json_or_query` value is new since the master plan listed these
axes, and is exactly the kind of shape that was absent from the tree
until it wasn't.

A few dozen cases, deterministic, well under a second. The oracle is
free: the source is constructed knowing where each parameter comes
from. This is what makes it different from mutating declarations in
the tree — flipping a declared location and asserting drift tests
the *comparison*, and every real defect was on the other side of it,
in the derivation.

No new dependency; `hypothesis` is not in the project and randomness
buys nothing over enumerating a space this small.

`tools/check-api-declaration-guards.sh` is unchanged. It proves the
guards fire, which is a different question from whether the
derivation is right.

**Ships nothing to production.** Its whole value is that it either
finds defects in `declarations.py` before compilation depends on it,
or it demonstrates there are none.

### PR 2 — Compile, wired but inert

Turn the declarations into marshmallow schemas and mount the
registry. Nothing validates yet; nothing changes for any caller.

* An `ARGTYPES` → marshmallow field mapping. The table already
  carries `type`, `format` and, for two tokens, `pattern`, plus the
  optional constraints element on 5 declarations. The mapping is
  mechanical, with three rules that are not:
  * **`netblock` compiles to a plain string.** It is deliberately
    format-only, with no pattern, because `NetworksEndpoint.post()`
    validates with `ipaddress.ip_network()`, which parses IPv6 too.
    Compiling a CIDR regex here would publish and then enforce an
    API narrower than the one that ships. Phase 2 wrote that
    reasoning down; phase 3 is where ignoring it would do damage.
  * **`uuidorname`, `namespace`, `node`, `url`, `ipv4` compile to
    plain strings.** Their prose `format` is documentation. Turning
    them into semantic validators is phase 6 (#534, #3269, #323,
    #936), and doing it here would smuggle enforcement into a
    warn-only phase.
  * **`binary` and the raw-body sentinel are excluded.** Upload
    bodies are not JSON and must never be parsed as such.
* The registry, built at mount time per D13, keyed by class and
  method.
* `required` is compiled as **metadata, not as a constraint**. The
  master plan already found `mode` on the agent-put endpoint
  declared required while omitting it has always been accepted.
  Enforcing required-ness is phase 6's decision; phase 3 records
  what it *would* have rejected so phase 6 has data.

Tests: every mounted handler compiles; the compiled field set
matches the declared parameter set for all 132 documented handlers;
`netblock` and the five prose-format tokens compile to unconstrained
strings; the three UNDOCUMENTED_BY_DESIGN handlers are absent by
name rather than by accident.

This PR should also reconcile with phase 2's `STRUCTURED_PARAMETERS`
table in `test_openapi_spec.py`. That table pins what the *published
specification* says about 16 parameters; the compiler is a second
consumer of the same declarations. They must not be able to
disagree — a test that walks both is cheap and closes the gap that
produced two consecutive rounds of type-token defects in phase 2.

### PR 3 — Validate, and warn

The behaviour-visible PR, and still not a behaviour change.

* A `validate_request` decorator inserted at index 0 of
  `method_decorators`, so it runs after authentication (an
  unauthenticated caller cannot probe the schema) and before every
  per-method decorator.
* A `@webargs_parser.error_handler` replacing webargs' default
  `abort(422)` with `sf_api.error(400, ...)`, per D4. **This fixes an
  existing defect as a side effect:** no error handler is registered
  today, so the three `json_or_query` sites and `blob.py`'s `query`
  site currently emit a raw 422 in webargs' own shape rather than
  the API's `{"error": ..., "status": ...}`. Worth a line in the
  release notes.
* Warn-only is the default and is controlled by one config setting,
  `API_VALIDATION_MODE`, with values `warn` and `enforce`. Phase 4
  flips the default; the setting exists from the start so the flip
  is a one-line, revertible change rather than a code change.
* The D12 collision marker in `log_request`, and deletion of the
  dead `passed_uuid` remap (D11).

**What a warn record contains.** One structured log line per
request that would have been rejected, at `info`, with:
`request-id` (already threaded), endpoint class and method, route,
parameter name, declared location and type, the reason, and the
offending value's **type**. Never its value — D5, and several of
these routes carry credentials, which is why `log_request` drops the
whole body on `handles_credentials()` routes rather than naming
fields.

Rejection reasons must be counted separately, because they answer
different questions:

| Reason | What a nonzero count means |
|---|---|
| type mismatch | the declaration is wrong, or callers send junk |
| unknown parameter (D10) | callers send keys we do not declare |
| missing required (D12) | `required` is over-declared; phase 6 input |
| body/path collision (D8) | the hazard is real in practice |
| constraint violation | the five bounds are wrong or callers exceed them |

Each record also carries **what the request returned anyway**. That
single field is what separates D10's two populations, and it is the
only way to measure the preemption cost: a warn whose request went
on to return 200 is a rejection enforcement would introduce, and a
warn whose request returned 404 is a status code enforcement would
change rather than a new refusal. Without it the warn log says how
often validation *would* fire but not what it would cost, which is
the question phase 4 has to answer.

That has a design consequence: the validator cannot log and move
on, because at index 0 the outcome is not known yet. It stashes the
pending warn on `flask.g` and something downstream emits it once the
response exists — the same request-scoped hand-off
`record_exception` uses for `_RECORDED_EXCEPTION_FIELDS`, and a
reason to build the telemetry as a Flask `after_request` hook rather
than as part of the decorator.

A prometheus counter with those as labels, alongside the log line,
so "is it quiet?" is answerable from a dashboard rather than by
grepping Loki. The label set is bounded and small; parameter name is
*not* a label.

### Then: measure

Deploy to sfcbr and read the logs. Per D5 the window is not a fixed
duration — it ends when every remaining rejection is intended — and
it must cover a full functional CI run plus seven days of sfcbr.

CI matters as much as sfcbr here, and for a different reason: sfcbr
has one workload driven mostly by the official client, while CI
exercises the ansible collection, which is a second implementation
of this API's contract and the one most likely to send something the
declarations do not describe (see #3308, where the collection's
networkspec parser makes every non-empty value truthy).

## Coordination and adjacencies

* **#3612** is the issue this phase's mechanism section describes,
  and D10 is where it actually gets closed — but not until phase 4,
  because warn-only still returns the interpreter text.
* **#1974 (pagination)** needs the bounded `limit`/`offset` types
  this compiles. Coordinate on the parameter types; the query and
  response-shape work stays in
  [`api-query-batching-roadmap.md`](api-query-batching-roadmap.md).
* **Phase 6** consumes the `required` and constraint warn counts.
  Nothing in phase 3 pre-empts its decision.
* **The client** (`shakenfist_client`, a separate repository) is not
  changed by this phase and must not need to be. If warn data shows
  the shipped client sends something the declarations reject, that
  is a declaration bug until proven otherwise — the client is the
  reference implementation of what the API accepts today.

## What this phase does not do

* **It rejects nothing.** Every request that succeeds today
  succeeds afterwards, with the same status and body.
* **No semantic validation.** MAC format, base64-ness, netblock
  overlap and the instance-create structures are phase 6.
* **No `required` enforcement.**
* **No response validation.** Out of scope by D7, not deferred.
* **It does not narrow `except TypeError`.** That is phase 5 and it
  is gated on phase 4: the broad catch is currently absorbing the
  malformed input this layer will start handling, and removing it
  before enforcement would turn 400s into 500s.

## Verification, phase-wide

* The derivation generator (PR 1) passes, and is demonstrated to
  fail when `declarations.py` is mutated — a generator that cannot
  fail proves nothing, which is the lesson the mutation harness
  exists to encode.
* `tools/check-api-declaration-guards.sh` still reports all
  mutations caught, with new mutations for the compilation path:
  a token compiled to the wrong field, a handler mounted without a
  schema, a semantic validator smuggled onto a prose-format token.
* The full unit suite passes; `pre-commit run --all-files` clean.
* The published specification still validates with zero errors, so
  phase 2's assertion holds and compilation has not perturbed
  rendering.
* Functional CI green, which for this phase is a *measurement* as
  much as a gate: a warn count of zero across a full CI run is
  evidence, and a warn count that is large is the finding.

## Success criteria

1. Every mounted handler resolves to a compiled schema, enforced by
   a test rather than by inspection.
2. Warn-only runs for a full CI run plus seven days on sfcbr.
3. Every remaining warn is classified: declaration bug, caller bug,
   or intended rejection. **The exit condition is that the list is
   explained, not that it is empty** — an intended rejection is a
   success, and phase 4 is what turns it into a 400.
4. No production behaviour change attributable to the phase.
5. A written recommendation for D10's `EXCLUDE`/`RAISE` choice,
   backed by the two undeclared-key populations — reaches-handler
   versus answered-first — rather than by taste, together with an
   estimate of how many requests change status code at enforcement
   because validation preempts a per-method decorator.

## Outcome

To be filled in as the phase lands.
