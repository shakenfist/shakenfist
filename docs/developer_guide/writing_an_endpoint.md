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

Each parameter is a five-element tuple:

```
(name, location, type, description, required)
```

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
so the "declared but not accepted" check knows to let it through.

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

## What is not checked yet

The declarations are documentation today. Compiling them into
per-endpoint request validation is phase 3 of the plan; until then a
correct declaration does not stop a caller sending something else.
Two known gaps:

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
* Swagger 2.0 allows at most one `in: body` parameter per operation,
  carrying a `schema` rather than a `type`. `swagger_helper()` emits
  one parameter per declaration, so operations with several body
  parameters render an invalid specification — **29 of 126 operations
  today, up from 23 of 124 before the declaration audit**, because
  correcting a parameter to `body` is individually right and moves this
  count the wrong way. The published specification is not yet
  linter-clean. Phase 2 collapses the parameters into a generated
  `schema`;
  [issue #3626](https://github.com/shakenfist/shakenfist/issues/3626)
  tracks the specification-validation test that will prove the count
  reaches zero rather than asserting it.
