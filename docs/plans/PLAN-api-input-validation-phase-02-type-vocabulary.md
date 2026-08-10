# Phase 2: Type vocabulary and a valid published specification

Phase 2 of [PLAN-api-input-validation](PLAN-api-input-validation.md).
Phase 1 made every individual parameter declaration correct; this
phase makes the *rendered* specification valid, measurable in CI,
and expressive enough to carry the bounds phase 3 will compile.

## Context

Phase 1 (PR #3620) corrected ~120 declarations and added the audit
that keeps them correct, but deliberately did not change how
`swagger_helper()` renders them. The published specification is
therefore still invalid OpenAPI 2.0, and nothing in CI measures
that. Measured with `openapi_spec_validator` over the flasgger
output at the end of phase 1: **129 validation errors**, in
exactly two classes:

* **128 from body parameters.** Swagger 2.0 permits at most one
  `in: body` parameter per operation, and it must carry a
  `schema` rather than `type`/`format`. `swagger_helper()` emits
  one `type`/`format` parameter per declaration, so the 32 of 132
  operations that declare more than one body parameter render an
  invalid operation — and even single body parameters carry
  `type` where a `schema` is required.
* **1 from `schemes`.** `API_ADVERTISED_HTTP_SCHEMES` is typed
  `str` in `config.py` (its own description says "space separated
  list") and `app.py` feeds it straight into the top-level
  `schemes` key, which must be an array of strings. A default
  deployment publishes `schemes: 'http'`. Pre-existing
  (`01ef8a563`), and the *first* thing a validator trips over.

A third defect found while planning this phase: the flasgger
template in `app.py` defines no `securityDefinitions`, so the
`security: [{'bearerAuth': []}]` requirement phase 1 attached to
every authenticated operation references a scheme the document
never defines. A generated client has no way to learn that
`bearerAuth` means "Authorization header carrying a JWT".

The master plan's phase table also assigns D9 here: new type
tokens (`unsignedinteger`, `macaddr`, `base64`, `netblock`) and
an optional constraints element (`minimum`/`maximum`/`pattern`),
rendered into the published OpenAPI so bounds are visible to
callers rather than invisible the way the events `limit` cap was.

## Shape of the work: three PRs, not one

Phase 1 shipped as a single PR and took twelve review rounds,
four of which found defects in machinery added the round before.
The review loop converges faster on small diffs, and each step
below is independently landable and independently valuable. The
ordering is forced anyway: the master plan records that the
validation test is worth having *before* the renderer fix,
because it turns "invalid in N places" into a number that moves.

| PR | Delivers | Ratchet after |
|----|----------|---------------|
| 1 | Specification-validation test (#3626); `schemes` and `securityDefinitions` template fixes | 128 errors, one class |
| 2 | Collapse body parameters into one schema-carrying parameter | 0 errors |
| 3 | New type tokens and the constraints element, applied where the issue list demands | 0 errors, richer spec |

### PR 1 — the validation ratchet and template fixes

**Closes #3626** (the test is the issue; the remaining error
class it measures is PR 2's job).

1. **Add `openapi_spec_validator` to the `test` extra** in
   `pyproject.toml` (Apache-2.0, so the license comment pattern
   holds). It is a test-only dependency; the API daemon never
   validates its own spec at runtime.

2. **New test module**
   `shakenfist/tests/external_api/test_openapi_spec.py`. The unit
   test suite already builds the real Flask app with mocks
   (`test_health_endpoints.py` is the minimal pattern: set
   `external_api.TESTING`, pin `config.NODE_UUID`, use
   `external_api.app.test_client()`). The test fetches
   `/apispec_1.json` (flasgger 0.9.7.1's default specs route —
   verify it is served unauthenticated, since flasgger registers
   it directly on the Flask app rather than through
   `api_base.Resource`; if it is not reachable, fall back to
   `swagger.get_apispecs()` inside an app context) and runs
   `openapi_spec_validator.validate()` over it.

   The assertion is a **ratchet with an exact count, not a
   ceiling**: iterate the validator's errors, classify each
   against a small table of known classes (initially just
   "multiple/typed body parameters"), fail on any error outside
   the table, and assert the total equals the recorded number.
   Exact equality means a new endpoint that adds another
   multi-body operation fails CI instead of quietly raising the
   count — the same honesty rule the phase 1 audit applies to
   declarations. When PR 2 lands, the table empties and the test
   collapses to "the specification is valid", which is its
   permanent form.

3. **Fix `schemes`.** Split in `app.py`:
   `config.API_ADVERTISED_HTTP_SCHEMES.split()`. The config
   field's documented contract is already "space separated
   list", so the consumer honours it; changing the field to
   `list[str]` would change the environment-variable format for
   every deployment to fix a rendering bug, which is the wrong
   trade. One-scheme deployments publish `['http']`, two-scheme
   deployments finally publish two entries.

4. **Define `securityDefinitions`** in the flasgger template:

   ```python
   'securityDefinitions': {
       'bearerAuth': {
           'type': 'apiKey',
           'name': 'Authorization',
           'in': 'header',
           'description': 'JWT bearer token, as "Bearer <token>".'
       }
   }
   ```

   Swagger 2.0 has no first-class bearer scheme (that arrived in
   OpenAPI 3); `apiKey` in the `Authorization` header is the
   standard 2.0 idiom. This makes the per-operation `security`
   requirement resolvable and lets generated clients attach the
   header automatically.

5. **Rider: issue #3643.** The locale-dependent `open()` calls in
   `test_parameter_declarations.py` get `encoding='utf-8'` while
   a PR is already touching the test tree. Trivial, and keeps the
   issue from going stale. (`Fixes #3643` in the commit that does
   it.)

### PR 2 — one body parameter per operation

A change to the renderer only: declarations keep their
five-element shape, the audit and fixer read tuples via AST and
never see rendered output, so neither needs to change.

In `swagger_helper()`, parameters declared `body` no longer
append individual entries. They accumulate, and after the loop
render as a single parameter:

```python
{
    'name': 'body',
    'in': 'body',
    'required': <any body declaration is required>,
    'schema': {
        'type': 'object',
        'properties': {
            <name>: {'type': ..., 'format': ...,
                     'description': <argdescription>},
            ...
        },
        'required': [<names declared required>],  # omitted if empty
    }
}
```

Rules and edge cases, enumerated up front (the phase 1 review
loop existed because edges were found one round at a time):

* **Zero body declarations** — no body parameter is emitted at
  all. Eight operations have empty parameter lists; more have
  only path/query parameters.
* **One body declaration** — still collapses. A single body
  parameter carrying `type` instead of `schema` is just as
  invalid as three of them.
* **`RAW_BODY_PARAMETER`** — a declaration named `body` with type
  `binary` means "the raw request body", renders as
  `schema: {'type': 'string', 'format': 'binary'}` with no
  object wrapper, and **must be the only body declaration** on
  its operation: raw bytes and named JSON keys cannot share a
  request body, so mixing them raises `InvalidAPIDeclaration` at
  import time like every other malformed declaration.
* **A named parameter that happens to be called `body`** with a
  non-binary type is not the raw marker; it becomes a property
  like any other. No collision: the wrapper's `name: 'body'`
  lives at parameter level, properties live inside the schema.
* **`required` inside a schema is a JSON Schema array of property
  names** — a different thing from the parameter-level boolean —
  and an *empty* `required` array is itself invalid, so it is
  omitted when no body property is required. The wrapper's
  parameter-level `required` is true iff any property is.
* **Descriptions survive** as per-property `description` keys.
  The prose "formats" phase 1 kept (`'a JSON dictionary'` etc.)
  ride along unchanged; `format` is an open string in schema
  objects too. Turning `arrayofstring`/`arrayofdict` into real
  `type: array` schemas is deliberately deferred to PR 3's
  vocabulary work — one behaviour change per PR.

Verification for this PR:

* The ratchet count goes 128 → 0 and the test's known-class
  table empties; the test now asserts plain validity.
* Direct unit tests of `swagger_helper()` output in
  `test_parameter_declarations.py`'s
  `SwaggerHelperValidationTestCase`: multi-body collapse shape,
  single-body collapse, raw-body rendering, the
  raw-plus-named-body rejection, empty-required omission.
* `tools/check-api-declaration-guards.sh` still passes — the
  mutations target declarations, which have not changed shape.
* The generated OpenAPI published at openapi.shakenfist.com
  changes shape for every operation with a body: release note
  required. The official Python client does not read the spec,
  so nothing breaks operationally; anyone *generating* a client
  finally can.

### PR 3 — type tokens and the constraints element (D9)

Two vocabulary changes to `swagger_helper()`, then their
application across the tree.

**New tokens** in `argtypes`:

| Token | Renders as | For |
|-------|-----------|-----|
| `unsignedinteger` | `{'type': 'integer', 'format': 'int64', 'minimum': 0}` | artifact `max_versions` (negative is silently destructive — `delete_old_versions()` slices `[:-max]`), version indexes, blob offsets |
| `macaddr` | `{'type': 'string', 'format': 'mac address', 'pattern': <colon-separated hex>}` | #534 |
| `base64` | `{'type': 'string', 'format': 'byte'}` — `byte` is Swagger 2.0's standard token for base64 | user data, #3269 |
| `netblock` | `{'type': 'string', 'format': 'CIDR netblock', 'pattern': <a.b.c.d/n>}` | network create, #323 |

`arrayofstring` and `arrayofdict` also become real schemas here
(`type: array` with `items`) now that body rendering goes through
schema objects where `array` is legal.

**The constraints element**: an optional sixth tuple element, a
dict whose keys are drawn from `{'minimum', 'maximum',
'pattern'}`. Validated at import time in the established style —
every malformed declaration raises `InvalidAPIDeclaration`:

* arity check becomes "5 or 6 elements";
* a sixth element must be a dict with only known keys;
* `minimum`/`maximum` must be numbers, `pattern` must compile
  under `re.compile`;
* a constraint that contradicts its token (a `minimum` on a
  `string`, a second `minimum` on `unsignedinteger`) is rejected
  rather than merged silently.

Constraints render directly onto query/path parameters (valid
Swagger 2.0 parameter keywords) and into body schema properties
(valid JSON Schema keywords) — the ratchet test proves both stay
valid.

**Applications in this PR** (documentation-layer only; nothing is
*enforced* until phases 3–4 compile and turn on rejection):

* events `limit`: `{'minimum': 0, 'maximum': 1000}` — the cap
  exists in code today and is invisible to callers, the original
  D9 motivating case;
* blob read `offset`/`limit` and upload truncate `offset` →
  `unsignedinteger`;
* artifact `max_versions` → `unsignedinteger`;
* interface MAC on instance create → `macaddr`;
* instance `user_data` → `base64`;
* network `netblock` → `netblock` (the reserved-range *semantic*
  check stays in phase 6).

**Machinery updates forced by the sixth element:**

* `test_parameter_declarations.py`'s AST walk and
  `tools/fix-api-parameter-locations.py` both destructure
  declaration tuples; both learn the optional element. The
  fixer's byte-splice targets element `[1]` by AST node offsets,
  so a sixth element does not move what it splices — verify with
  a fixture rather than asserting it in review.
* `tools/check-api-declaration-guards.sh` gains mutations: a
  six-element tuple with an unknown constraint key, a non-dict
  sixth element, an uncompilable `pattern`, a `minimum` on a
  string token. Run the harness; every new guard must be caught,
  not read.
* `docs/developer_guide/writing_an_endpoint.md`: tuple shape
  becomes "five elements, or six when constrained", new token
  table, constraints reference.
* **Rider: issue #3642.** The variadic-handler vacuous pass in
  the accepted-parameters check is audit machinery this PR is
  already editing; fix it here (`Fixes #3642`).

## Coordination and adjacencies

* **#1974 / api-query-batching-roadmap** — the bounded
  `limit`/`offset` types PR 3 defines are exactly what pagination
  needs. Coordinate on the tokens; the query and response-shape
  work stays in that roadmap.
* **#3616 (mypy for `external_api/base.py`)** — the master plan
  lists it as enabling. Optional rider on PR 2, which rewrites
  the renderer anyway; take it if the annotation diff stays
  small, defer without guilt otherwise.
* **The autofixer** may pick up filed issues; #3642 and #3643
  have sat since 2026-08-03 untouched, but label them
  `automated-fix-attempted` when their carrying PR branches, so
  an automated fix does not race the in-flight work.
* **openapi.shakenfist.com** republishes from the tree, so PR 2's
  shape change propagates on the next docs sync; nothing manual.

## What this phase does not do

* No request is validated or rejected — compilation is phase 3,
  enforcement phase 4. Everything here changes what is
  *published* and what the compiler will later have to work with.
* No `required` semantics change (phase 6).
* No response validation (out of scope by D7).
* No semantic validators (netblock overlap, MAC uniqueness) —
  the tokens publish the format; cluster-state checks are
  phase 6.

## Verification, phase-wide

* The ratchet number: 129 before PR 1, 128 after it, 0 after
  PR 2, still 0 after PR 3. Each PR's commit message records the
  measurement.
* `tools/check-api-declaration-guards.sh` after every PR — a
  guard that passes on a deliberately broken tree is not a
  guard, and the harness grows with PR 3's constraint checks.
* `pre-commit run --all-files` before every commit; the
  `check-api-parameter-locations` hook must stay green through
  the tuple-arity change.
* Adversarial pass per the review-loop lessons: enumerate the
  declaration input space (0/1/N body parameters, raw body,
  raw-plus-named, five- and six-element tuples, every new token,
  every constraint key, contradictory constraints) rather than
  sampling what the tree happens to contain today.

## Success criteria

- [x] CI fails when the generated specification acquires a new
      validity error class (#3626 closed).
- [x] `openapi_spec_validator` reports zero errors on the
      generated specification.
- [x] `securityDefinitions` published; `security` requirements
      resolvable; `schemes` an array.
- [x] Every operation renders at most one body parameter, always
      schema-carrying.
- [x] The four D9 tokens and the constraints element exist,
      import-time validated, mutation-tested, documented.
- [x] The events `limit` bounds are visible in the published
      OpenAPI.
- [x] #3642 and #3643 closed as riders.
- [x] Master plan phase table, `docs/plans/index.md`, and
      `writing_an_endpoint.md` updated; release note for the
      published-spec shape change.

## Outcome

Implemented 2026-08-08 as three commits on
`api-input-validation-phase-02`, one per planned PR scope, so the
work can land as one PR or be split back into three — that is a
landing decision, not an implementation one. Measurements landed
where the plan predicted: 129 errors before the work, 128 after the
`schemes` fix (the ratchet's one recorded value), zero after the
collapse, still zero with the vocabulary applied. The mutation
harness grew from 12 to 24 across the review rounds: first the four
constraint-validation guards (all `caught-import`), then one per
defect review turned up, so each new guard is demonstrably able to
fail on the tree that shipped without it. Its NO-OP verdict earned
its keep during the work: retyping the blob `offset`/`limit`
declarations to
`unsignedinteger` moved them out from under mutation 3's search
text, which reported NO-OP instead of a false catch.

Three deviations from the letter of the plan, all recorded rather
than silent:

* **`macaddr` is defined but unapplied.** No declaration site
  exists for it: interface MACs arrive inside the `network`
  arrayofdict specs on instance create, not as a standalone
  parameter, so its first consumer is the structured value types
  work (#936, phase 6). The token ships now because D9 committed
  the vocabulary and the pattern is mutation-tested either way.
* **The events `limit` minimum is 1, not 0.** The server replaces
  `limit <= 0` with the default 100 (the mariadb limit-hardening
  rules), so 0 is *accepted* but meaningless; the published bound
  documents the values a caller can usefully send. Whether phase 4
  enforcement should reject 0 or keep tolerating it is a warn-only
  question for phase 3's sfcbr data.
* **`netblock` ships without its planned pattern.** The table above
  specifies `<a.b.c.d/n>`, but `NetworksEndpoint.post()` validates
  with `ipaddress.ip_network()`, which parses IPv6 as well, so an
  IPv4-only pattern would publish the API as narrower than it is —
  and phase 4 would then compile a documentation commit into a 400
  for input that works today. The pattern was also loose enough to
  admit `999.999.999.999/99`, so it was not carrying its weight as
  validation either. `netblock` is therefore a format-only token and
  `ip_network()` remains the single source of truth for what parses.
  The reserved-range semantic (#323) was always out of scope.

Review of the implementation found one real defect the vocabulary
work created rather than inherited: instance create declared
`metadata` as `arrayofdict` while the handler answers 400 to anything
but a dictionary. That was inert prose before this phase and a
machine-readable assertion of the wrong shape after it -- exactly the
trap the `netblock` decision above avoids, arrived at from the other
direction. `metadata` is now `dict`, and the `dict` token renders as
a real `{'type': 'object'}` for the same reason the array tokens do,
which also corrects `video` and `bound_claims`. `test_openapi_spec.py`
pins the published shape, since a token swap is legal at import time
and only the endpoint's semantics distinguish the two.

A third round found the same defect class again in the opposite
direction: console `length` was retyped to `unsignedinteger` while
`-1` is a supported sentinel meaning "the whole log", used by the
functional suite itself. Two instances of one class, both found by
review rather than by a machine, is the signal that the class needed
a mechanism rather than another fix. Types are not derived from
anything -- `declarations.py` reads a declaration's name and location
and never looks at its type -- so
`test_openapi_spec.STRUCTURED_PARAMETERS` now lists every parameter
publishing a structure or a bound alongside the shape its handler
actually accepts, and an entry describes that shape in full: a
constraint key the entry does not list must not be published. Both
shipped defects fail that table, verified by mutation (20 and 21 in
the guard harness), as does a spurious `maximum` on an entry which
asserts no bound (mutation 24) and a bound on a parameter nobody
registered (mutation 23). It remains a registry rather than a
derivation of *what* a handler accepts, so it constrains the next
author to think rather than proving them right; only its completeness
is derived. A real derivation of types from handler bodies is phase
3's problem, where the warn-only rollout measures declarations
against live traffic.

The `unsignedinteger` comment named a live data-loss foot-gun --
a negative `max_versions` deletes the oldest version on every index
add -- so this phase closes it rather than documenting it and waiting
for phase 4. The first attempt closed it in
`ArtifactMaxVersionsEndpoint.post` alone, which a later review round
correctly called one of *three* routes writing the attribute: label
create and instance snapshot both pass a caller's `max_versions`
into `Artifact.new()`, which reaches the same setter and the same
`delete_old_versions()`. The coercion and the check now live in
`artifact.validated_max_versions()`, called from the setter so every
writer inherits the guard whether or not it ever sees a request
body, and from all three handlers so the refusal is a 400 rather
than a 500. `TypeError` is caught alongside `ValueError` so a list
or dict body value is a 400 too.

The same "publish only what the server backs" rider applies to the
blob data route, whose `offset` and `limit` now publish minimum 0.
Unbacked, both failed worse than meaninglessly: a negative offset
reached `f.seek()` inside `stream_with_context`, so the `OSError`
arrived after the 200 had begun and the caller saw a truncated body
rather than an error, and a negative limit read to EOF and defeated
the cap it was asked for. Both are checked in the handler. A
marshmallow `validate=Range(min=0)` on the webargs schema was tried
first and rejected: webargs raises `UnprocessableEntity` and the
app's error handler renders it as a **500**, which is the same
serialisation hazard the `json_or_query` loader in `base.py`
documents. Nothing in the tree uses `validate=` today, so the
webargs error path is a latent phase 3 problem rather than a live
one, but it is worth knowing before phase 3 reaches for it.

Those, and the `key_ttl` bounds below, are the only behaviour changes
in a phase which is otherwise documentation.

A related judgement call, made when the review raised it: `cpus`,
`memory`, `version_id` and console `length` are typed
`unsignedinteger` (minimum 0) rather than carrying a `{'minimum': 1}`
constraint. Nothing in the create path rejects a zero or negative
`cpus` today — the scheduler's `_has_sufficient_cpu()` compares
`current + cpus > hard_max`, which a negative passes — so minimum 0
is the strongest claim the server actually backs. Tightening to 1 is
a phase 3 warn-only question, decided with data, not a documentation
change.

`key_ttl` was in that list and should not have been: it is the one
member of it the server already bounds on both sides.
`validate_key_ttl()` refuses `<= 0` and refuses anything above
`MAX_KEY_TTL_SECONDS` (86400), and both rule endpoints turn the
resulting `RuleValidationError` into a 400. Typed
`unsignedinteger` it published a minimum of 0 — documenting a value
the server answers 400 to — while the enforced cap stayed invisible,
which is the very invisible-cap problem the events `limit` change in
this phase exists to fix. Both declarations now carry
`{'minimum': 1, 'maximum': MAX_KEY_TTL_SECONDS}`, sourced from the
constant rather than restated, so the specification cannot drift from
the check.

Two decisions the review asked to see recorded rather than inferred:

* **The events `limit` maximum of 1000 will not compile into a
  rejection.** `get_events_for_object()` *clamps* rather than
  refuses, so `limit=5000` succeeds today and returns 1000 rows.
  Phase 4 must keep clamping. The published maximum is a statement
  of what a caller can usefully ask for, not a promise that asking
  for more is an error, and turning it into a 400 would be a
  documentation commit changing wire behaviour two phases later —
  precisely the trap the `netblock` decision avoids.
* **`CONSTRAINT_KEYS` is deliberately three keys.** `maxLength`,
  `minLength`, `minItems` and `enum` are all valid Swagger 2.0
  keywords and all have real consumers waiting — rule `name` is
  refused above 255 characters, `MAX_SCOPE_LENGTH` bounds each
  scope, `scopes` must be non-empty, `configdrive` accepts only
  `none` and `openstack-disk` — but this phase set out to publish
  the numeric and pattern bounds D9 named, and each new key needs
  its own validation and its own mutation. They are scoped out, not
  overlooked; phase 3 is where the compiler makes the case for
  adding them concrete.

Finally, `STRUCTURED_PARAMETERS` grew a derived completeness
assertion. As shipped it was a hand-maintained list which could fall
silently behind the tree — the same failure mode as the prose types
it replaced, one level up, and it was already missing twelve of the
tree's structured or bounded declarations when the review counted
them.
`test_every_published_structure_or_bound_is_registered()` now walks
the published specification, collects every parameter carrying an
`object`/`array` type or any key in `CONSTRAINT_KEYS`, and fails
until each one has an entry. The entries themselves still have to be
written by hand against the handler, which is the point: the
derivation says *something is missing*, and a human still has to say
what the handler really accepts. An entry now also describes the
published shape in full — a constraint key it does not list must not
be published — so a `maximum` appearing on console `length` fails
the same way a `minimum` already did.
