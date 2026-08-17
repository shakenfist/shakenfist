Writing an API endpoint
===

Every REST endpoint declares the parameters it accepts, and those
declarations are checked. Getting one wrong is not a documentation bug
that lands quietly: `swagger_helper()` validates each declaration when
the module is imported, so a malformed one raises
`shakenfist.exceptions.InvalidAPIDeclaration` and `sf-api` does not
start.

This page is the reference for what a declaration has to look like.
The reasoning behind the rules, and the audit that found ~120 wrong
declarations already in the tree, is in
[PLAN-api-input-validation](../plans/PLAN-api-input-validation.md).

## The shape of a handler

This is `InstanceEndpoint.get`, verbatim from
`shakenfist/external_api/instance.py`:

```python
class InstanceEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Get instance information.',
        [('instance_ref', 'path', 'uuidorname',
          'The UUID or name of the instance.', True),
         ('namespace', 'body', 'namespace',
          'Scope the name lookup to this namespace.', False)],
        [(200, 'Information about a single instance.', instance_get_example),
         (404, 'Instance not found.', None)]))
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.log_token_use
    def get(self, instance_ref=None, instance_from_db=None, namespace=None):
        return instance_from_db.external_view()
```

Each parameter is a five-element tuple, with an optional sixth
element for constraints:

```
(name, location, type, description, required[, constraints])
```

The type is a token from `api_base.ARGTYPES` — read that table for the
current vocabulary; an unknown token is rejected at import time. Beyond the
obvious primitives it includes `unsignedinteger` (an integer whose
published `minimum` is 0 — use it for anything where a negative value
is meaningless or destructive, like `max_versions`), `base64` (a
string whose published format is Swagger 2.0's standard `byte` token),
`macaddr` (a string carrying a published validation `pattern`),
`netblock` (format only — `ipaddress.ip_network()` in the handler
stays the single source of truth for what parses, and a published
IPv4 pattern would describe the API as narrower than it is), and real
array types for `arrayofstring`/`arrayofdict` and a real object type
for `dict`. Objects, and arrays of objects, can only be declared in
the body: outside one there is no schema object to nest a structure
in, so they are rejected at import time.

Declare the token that matches what the handler accepts, and **publish
what the server backs**: a bound tighter than the server's own
behaviour belongs in the specification only where the server already
coerces or rejects outside it. The events `limit` publishes a minimum
of 1 because the server replaces anything lower with the default;
`cpus` and `memory` publish 0 rather than 1 because nothing rejects a
zero today, and tightening them is a decision to be made once there is
warn-only data to make it with.

Nothing derives a type from the handler the way locations are derived,
so getting this wrong is invisible until a client generator or the
constraint compiler acts on it. Two examples, both caught in review by
a human rather than by a machine:

* `metadata` on instance create was declared `arrayofdict` while the
  handler answers 400 to anything but a dictionary — harmless while
  the token rendered as prose, a positive assertion of the wrong shape
  once the type became machine readable.
* console `length` was declared `unsignedinteger` while `-1` is a
  supported sentinel meaning "the whole log", which the functional
  suite itself relies on — publishing the API as narrower than it is.

The check against both is `STRUCTURED_PARAMETERS` in
`shakenfist/tests/external_api/test_openapi_spec.py`: every parameter
publishing a structure or a bound is listed there with the shape the
handler actually accepts, and an entry describes that shape in full —
a constraint key the entry does not list must not be published, which
is how the console `length` entry asserts that nothing bounds it in
either direction.

You do not have to remember to add an entry.
`test_every_published_structure_or_bound_is_registered` derives the
set which ought to be listed from the published specification and
fails until each member has one. What you do have to do is read the
handler before writing the entry: the derivation can say that
something is missing, but only you can say what the handler really
accepts, and the point is agreement with the code rather than with
the declaration.

The constraints element is a dict with keys drawn from `minimum`,
`maximum` and `pattern`. All three are valid Swagger 2.0 keywords, so
a bound renders into the published OpenAPI instead of living only in
code — the events `limit` cap was invisible to callers for years for
exactly that reason, and now reads:

```python
('limit', 'body', 'integer',
 'The number of events to return, defaults to 100 and is '
 'capped at 1000.', False, {'minimum': 1, 'maximum': 1000})
```

Constraints are validated at import time in the same style as
everything else: unknown keys, non-numeric bounds, bounds on
non-numeric types, contradictory bounds, patterns that do not
compile, patterns on non-string types, unanchored patterns, and a
constraint restating a key its type token already renders (a second
`minimum` on `unsignedinteger`) all raise `InvalidAPIDeclaration`.
A pattern must be `^...$` anchored, with no top-level alternation
(`^a|b$` is anchored on neither branch — wrap it in a group), because
its two consumers read anything looser differently: JSON Schema
`pattern` is an unanchored *search*, while the compiled validator
requires the whole value to match (`re.fullmatch`, chosen because
Python's `$` also matches before a trailing newline and ECMA-262's
does not). Fully anchored and group-wrapped is the one form both read
identically, so it is required rather than documented. Note that
declared constraints are *published documentation* until [PLAN-api-input-validation](../plans/PLAN-api-input-validation.md)
compiles them — a constraint does not reject anything yet.

Three keys, deliberately. `maxLength`, `minLength`, `minItems` and
`enum` are equally valid Swagger 2.0 keywords with real consumers
waiting for them — a rule `name` is refused above 255 characters,
`scopes` must be non-empty, `configdrive` accepts only two values —
but each needs its own import-time validation and its own mutation in
the guard harness, and the current scope is the numeric and pattern
bounds. They are scoped out rather than overlooked; see [PLAN-api-input-validation](../plans/PLAN-api-input-validation.md)
for where the case for adding them gets made.

**There is no per-handler authentication decorator.** Authentication is
the default, applied to every handler by `_authenticate_unless_public`
in `api_base.Resource.method_decorators`; an endpoint opts *out* rather
than in. Decorator order matters, and the outer decorator runs first —
so `@swag_from` goes outermost, above the `@api_base.arg_is_*` and
ownership decorators, which is what every handler in the tree does. See
the banner comment in `external_api/app.py`.

Note that `instance_from_db` is injected by `@arg_is_instance_ref` and
is not a parameter: it is not declared, and declaring it fails the
audit.

## The rules

**1. Every handler carries a declaration.** A `get`, `post`, `put` or
`delete` on a `Resource` subclass must have a
`@swag_from(api_base.swagger_helper(...))`, even if it takes no
parameters — an empty parameter list is a legitimate declaration, and
eight endpoints have one. A handler with no `swag_from` at all is
absent from the published OpenAPI, so a generated client cannot call
it. The only exceptions are the unauthenticated health probes, which
are listed in `UNDOCUMENTED_BY_DESIGN` in
`shakenfist/tests/external_api/test_parameter_declarations.py`.

**2. `location` must be one of the OpenAPI 2.0 set** —
`api_base.SWAGGER_PARAMETER_LOCATIONS`: `path`, `query`, `body`,
`header` or `formData`. It must also be the location the parameter
*actually* arrives at, which is derivable from the code:

| The parameter is | Its location is |
|---|---|
| a segment of the mounted route in `app.py` | `path` |
| in a schema bound with `@use_kwargs(..., location='query')` | `query` |
| read from `flask.request.args` | `query` |
| anything else | `body` |

That last row is the default because `log_request` merges the JSON
request body into the handler's kwargs, and the official client sends
a JSON body for every method including `GET`.

**3. A `path` parameter must be `required=True`.** The route cannot
match without it, and an optional path parameter makes the published
specification invalid — client generators reject it outright.

**4. A raw request body is declared as `api_base.RAW_BODY_PARAMETER`.**
Upload endpoints read the body from `flask.request` rather than
receiving it as a kwarg. Use the constant rather than inventing a name,
so the "declared but not accepted" check knows to let it through. An
operation cannot declare both the raw body and named body parameters —
raw bytes and JSON keys cannot share a request body, and
`swagger_helper()` rejects the combination at import time.

**A note on rendering:** declarations stay one tuple per parameter,
but Swagger 2.0 permits at most one `in: body` parameter per
operation, carrying a `schema`. `swagger_helper()` therefore collapses
an operation's body declarations into a single generated body
parameter whose schema has one property per declaration. You declare
parameters individually; the collapse is the renderer's business.

**A constraint on routing rather than on declarations:** a class
mounted on more than one route must carry the same parameters on
each. The derivation merges a class's routes and refuses to proceed
when their parameter sets differ, so mounting one `Resource` on both
`/things` and `/things/<thing_ref>` fails the audit. Split the
collection and item endpoints into two classes, which is what the
tree does everywhere (`Readyz`, the only class mounted twice, has two
parameter-free routes).

**5. Every kwarg the handler accepts is declared.** An accepted-but-
undeclared parameter is invisible to anyone reading the API, which is
how `sshkey` and `userdata` sat in the published specification for
years while the handler read `ssh_key` and `user_data`. If a kwarg
genuinely must not be published, add it to `UNDECLARED_BY_DESIGN` with
a reason. Objects the decorators inject — anything ending in
`_from_db` — are not parameters and must not be declared.

A handler must therefore name its parameters: `*args` or `**kwargs` in
the signature accepts body keys nothing can enumerate (`log_request`
merges the whole JSON body into the handler's kwargs), so the audit
refuses to proceed rather than reporting a tree it could not check.

**A note on responses:** nothing validates the response list, and one
response is not declared per endpoint at all. `handle_database_unavailable`
sits in `Resource.method_decorators`, so **every** endpoint can answer
`503` when the database tier is unreachable — the published
specification declares it only on the handful of endpoints where that
was an observable change of behaviour rather than a new failure mode
(`GET /blobs`, which used to answer `200` with an empty list, and the
federated token endpoint). Treat `503` as a global response when
writing a client; declare it on your endpoint only if you have
something endpoint-specific to say about it.

## Checking your work

The audit derives the correct location for every declaration and
compares it against what is declared:

```bash
python3 tools/fix-api-parameter-locations.py            # report drift
python3 tools/fix-api-parameter-locations.py --apply    # correct it
```

The same derivation runs as a pre-commit hook
(`check-api-parameter-locations`) and, more importantly, as a unit
test — `shakenfist/tests/external_api/test_parameter_declarations.py`,
which is what CI actually enforces, since no workflow runs pre-commit.

`tools/check-api-declaration-guards.sh` mutation-tests those
assertions: it breaks each property on purpose and confirms the guard
fires. Run it if you change the derivation or add an assertion, since
a guard that passes on a deliberately broken tree is not a guard.

Those assertions compare declarations against the derivation, so they
are only as good as the derivation itself — and the tree can only
exercise the source shapes it happens to contain.
`shakenfist/tests/external_api/test_derivation_generator.py` covers
the rest: it crosses route form, `@use_kwargs` binding and
`flask.request.args` read style, builds a synthetic endpoint for each
of the 225 combinations, and asserts the derivation recovers what that
case was constructed to mean. Every defect found while building the
original declaration audit was a shape absent from the tree, which is
why this is generated rather than sampled. **If you add a way for a parameter to
arrive, add an axis value here** — a shape the generator does not
enumerate is a shape nothing checks.

## What validation does with them

The declarations are compiled into marshmallow schemas at startup
(`shakenfist/external_api/validation.py`) and checked against every
request. **Nothing is rejected**: `API_VALIDATION_MODE` defaults to
`warn`, which logs what would have been refused and changes no
response. Setting it to `enforce` answers `400` in the usual
`{"error": ..., "status": ...}` shape, and is the switch to throw once
the warn log is understood.

A warn record carries the endpoint, the parameter, the reason, the
offending value's **type** — never its value — and the status the
request went on to return anyway. That last field is the interesting
one: a finding on a request which returned 200 is a rejection
enforcement would introduce, while one on a request which returned 404
is a status code enforcement would merely change, because validation
runs ahead of the per-method decorators which produce those.

Reasons are counted separately because they answer different
questions: `unknown-parameter`, `type-mismatch`, `missing-required`
and `body-path-collision`.

Two things it deliberately does not do. `required` is recorded but
never enforced — not even in `enforce` mode, where missing-required
findings are filtered out of the rejection decision. Several
parameters are declared required while omitting them has always
worked, and what to do about that is still open — see [PLAN-api-input-validation](../plans/PLAN-api-input-validation.md).
And the prose `format` on a type token is documentation: `netblock`,
`uuidorname`, `namespace`, `node`, `url` and `ipv4` compile to plain
strings, because semantic validation of them is not built yet. Only
`type`, `pattern`, `minimum` and `maximum` constrain anything.

## What is not checked yet

Enforcement is off, so a correct declaration still does not stop a
caller sending something else. Two known gaps in the derivation
itself:

(The published specification itself *is* checked:
`shakenfist/tests/external_api/test_openapi_spec.py` validates the
generated OpenAPI with `openapi_spec_validator` on every CI run, so a
change that renders an invalid specification fails rather than
shipping — the fate of the 129 validation errors that were removed when
the type vocabulary landed.)

* `header` and `formData` cannot be derived from the code, so they are
  reported rather than corrected. No endpoint uses either today, and
  using one requires an `UNDERIVABLE_BY_DESIGN` entry in
  `test_parameter_declarations.py` — otherwise the declaration would be
  a silent opt-out from the whole audit.
* A schema bound with `@use_kwargs` must be a dict literal defined in
  the handler's own class or module. The derivation refuses anything it
  cannot resolve — a schema imported from another module, a marshmallow
  `Schema` class — and CI stays red until `declarations.py` is taught to
  read the new form. There is deliberately no allowlist for this,
  unlike locations: an unresolvable schema means the audit does not
  know which parameters arrive in the query string, so an opt-out
  entry would have to restate the schema's keys by hand — exactly the
  second source of truth this machinery exists to remove.
