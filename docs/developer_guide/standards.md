Concepts and Standards
===
# Ensuring a Common Language within the code base

This document records the standards and common language used within the Shaken Fist software system.

It should also record why the choice was made.

(This is actually just notes to save our future selves from tripping over the same problems.)

## Memory

Memory is measured in MiB in Shaken Fist. All references to memory size are stored and transmitted in MiB: Gigabytes can be too big if you want a lot of small machines. Kilobytes is just too many numbers to type. The ```libvirt``` API measures memory in KiB. Therefore, interactions with the library need to be careful to convert from MiB to KiB.

### Code Style

- Single quotes for strings, double quotes for docstrings
- 120 character line wrap
- Trim trailing whitespace
- See [CLAUDE.md](../../CLAUDE.md) for detailed style guide

### Attribute updates use field masks

The `update_*_attributes` functions in `shakenfist/mariadb.py` require a
`fields` argument naming exactly the model fields the caller changed;
only those columns are written to MariaDB. `fields=None` (write every
column) is reserved for row creation and pydantic-upgrade persistence.
An unmasked update is a cross-attribute lost update waiting to happen:
it pushes a stale snapshot of the other columns over concurrent
writers' committed changes. Relational data (like instance placement)
belongs in a table with per-row inserts and deletes — see the
`instance_location` rows in `object_references` — never in a JSON list
on an attributes row.

"The caller writes every column anyway" is not a reason to pass `None`.
`TrustedIssuer.update` and `MappingRule.update` both replace their whole
attribute set, because an issuer's URL and key source are one
configuration and a rule's policy is one unit — and both still name
every field. Naming them keeps `None` meaning only "creation or
upgrade", so a reader can tell the two cases apart, and it means the
day somebody adds a single-field writer they inherit a masked function
rather than having to retrofit one. The mask travels over gRPC as
`repeated string fields` on the request message, and the mock in
`shakenfist/tests/mock_mariadb.py` honours it too — a mock that
replaced the whole row would let a caller name the wrong fields and
still see the write it expected.

### Secret-carrying fields are `SecretStr`

Fields which hold a credential — `key` and `nonce` on
`NamespaceKeyAttributesData`, and the `AUTH_SECRET_SEED`,
`MARIADB_PASSWORD` and `LOKI_AUTH_HEADER` configuration options — are
`pydantic.SecretStr`, so stringifying one yields `**********` rather
than the value. Unwrapping with `.get_secret_value()` happens only at
named boundaries (the SQL writes, the gRPC encoder, the bcrypt
compare, the JWT claim), and each of those sites carries a comment
saying what breaks if the unwrap is missed. Do not add an unwrap to
make an error go away; move the value, or wrap the other side.

Two traps, both of which have already caused real bugs:

- **A `SecretStr` never compares equal to a `str`.** `SecretStr('x')
  == 'x'` is `False`, silently. Any comparison against a literal must
  unwrap — see `UNCONFIGURED_AUTH_SECRET_SEED` in `config.py`, which is
  a named constant precisely so the comparison is written once.
- **Assertions about secrets pass vacuously if you get them wrong.**
  `assertNotIn(attrs.key, some_string)` does not raise on a non-string
  needle under `testtools`, it just passes, always. And
  `assertNotIn(str(attrs.key), ...)` asserts that `'**********'` is
  absent, which is true of an event that leaked the real secret.
  Compare `.get_secret_value()`, and see `_assert_no_secret_material()`
  in `shakenfist/tests/test_namespace_key_object.py`, which is guarded
  by two tests proving it can still fail.

`SecretStr` maps to `VARCHAR(255)` in `schema/sqlalchemy.py`, the same
column `str` produces, so wrapping an existing field needs no
migration and no schema version bump. Deleting that mapping entry
would silently give fresh installs `LONGTEXT` while upgraded clusters
kept `VARCHAR(255)`, with no version change to notice. Full detail in
[`authentication.md`](authentication.md).

### Native ENUM columns and Python enums

A handful of columns are native MariaDB `ENUM` types built with
`sa.Enum(SomePythonEnum)` (e.g. `object_states.object_type`). MariaDB
freezes an `ENUM`'s value list at `CREATE TABLE` time, so adding a
member to the Python enum works on fresh installs but breaks existing
databases ("Data truncated for column", error 1265) — greenfield CI
will not catch this. You do NOT need to write a migration when adding
an enum member: `ensure_schema()` ends with a reconciliation pass
(`_ensure_native_enum_columns()` in `shakenfist/mariadb.py`) that
discovers every `sa.Enum` column from the SQLAlchemy metadata and
widens stale columns automatically. Unit coverage lives in
`shakenfist/tests/test_mariadb_enum_columns.py`; the live upgrade path
is exercised against a real MariaDB by the "Schema ENUM widening" CI
job in `functional-tests.yml` (`tools/ci-enum-widening-test.sh`).

### Documentation

- When a change adds, renames, or removes a user-visible concept
  (an object type, state, term, or similar), update
  [`glossary.md`](../glossary.md) in the same change so the
  glossary never drifts from the code.

### In-memory only objects never touch the database

Objects constructed with `in_memory_only=True` (the IPAM built when
hydrating a deleted network, blob-reference image artifacts) keep their
state, attributes and events in process memory. Any new persistence
path must be guarded on `self.in_memory_only`: a database row written
for an in-memory object is orphaned forever, because `hard_delete()`
early-returns for in-memory objects and state-driven iterators skip
objects whose static row is missing (issue 3532). Related uuid format
gotcha: `object_states.object_uuid` stores dashed uuids while `sa.Uuid`
static-table columns store undashed CHAR(32) — SQL joining the two must
transform one side (see the orphan reconciliation queries in
`mariadb.py`).

### API parameter declarations are enforced at import time

Every endpoint handler declares its parameters in
`swag_from(api_base.swagger_helper(...))`, and `swagger_helper()`
validates each declaration as the module is imported — so a malformed
one raises `InvalidAPIDeclaration` and **sf-api will not start**. The
rules: every handler carries a declaration (an empty parameter list is
valid, no declaration at all is not); `location` is one of
`SWAGGER_PARAMETER_LOCATIONS` *and* is where the parameter actually
arrives (route segment → `path`, `use_kwargs(location='query')` key or
`flask.request.args` read → `query`, otherwise `body`); a `path`
parameter must be `required=True`; a raw body is declared as
`api_base.RAW_BODY_PARAMETER` (and cannot be combined with named body
parameters); and every accepted kwarg is declared, excluding the
decorator-injected `*_from_db` objects. A declaration tuple has five
elements plus an optional sixth constraints dict
(`minimum`/`maximum`/`pattern`), also validated at import time. Body
declarations stay one tuple per parameter but *render* as a single
schema-carrying body parameter, because Swagger 2.0 allows only one;
objects and arrays of objects can only be declared in the body, since
outside one there is no schema object to nest a structure in. The
token vocabulary is `api_base.ARGTYPES`.

`shakenfist/external_api/declarations.py` derives the correct answer
from the source and is shared by the fixer
(`tools/fix-api-parameter-locations.py --apply`), the pre-commit hook
and `test_parameter_declarations.py`; `test_openapi_spec.py` validates
the generated specification itself in CI. Full reference in
`docs/developer_guide/writing_an_endpoint.md`.

### Events vs logs

Shaken Fist has two structured-record streams; choose the right
one when emitting a message:

- **If the message relates to one or more Shaken Fist objects**
  (instance, network, blob, artifact, …), emit an **event** via
  `eventlog.add_event()` / `add_event_multi()`. Events are the
  authoritative per-object record (stored in MariaDB, read back
  through the REST API) and also emit an `Added event` log line,
  so they appear in the log stream too.
- **If the message has no directly-associated object** (daemon
  lifecycle, scheduler decisions, node-level conditions), emit a
  **log** line via the module `LOG`.

Events stay authoritative in MariaDB — they are never moved to
Loki; logs ship to Loki (or the local journal). The `Added event`
echo into the log stream is controlled by `LOG_EVENTS_TO_LOKI`
(default on). See
[`logging.md`](../operator_guide/logging.md)
and [`events.md`](../operator_guide/events.md).

### Testing

```bash
tox                              # Run all tests
tox -eflake8                     # Lint check
tox -emypy                       # Type checking
tox -ecover                      # Coverage report
stestr run {test_name}           # Run specific test
```

### Pre-commit Hooks

The repository uses pre-commit hooks to validate code before commits:

```bash
pip install pre-commit           # Install pre-commit
pre-commit install               # Set up git hooks
pre-commit run --all-files       # Run all hooks manually
```

Current hooks:
- `actionlint` - Validates GitHub Actions workflow files
- `ansible-lint` - Validates the `shakenfist.shakenfist` Ansible collection
  (`shakenfist/deploy/collection/`)
- `flake8` - Style check via tox, on changed files
- `py3` - Unit tests via tox
- `check-from-db-by-ref-namespace` - Every `*_from_db_by_ref` call passes a
  namespace, so an endpoint cannot fetch across tenants
- `check-endpoint-authentication` - Endpoints inherit authentication from
  `api_base.Resource.method_decorators` rather than each carrying a
  decorator; this rejects resources that subclass `flask_restful.Resource`
  directly, and `@api_base.public` markers that are not the outermost
  decorator
- `check-api-parameter-locations` - Every `swagger_helper()` parameter is
  declared at the location it actually arrives at
- `mypy` - Type checking via tox (incremental rollout)

Note that no workflow runs pre-commit, so a hook only fires for
contributors who have run `pre-commit install`. A check that must hold
in CI needs a unit test as well — which is why the parameter-location
derivation is shared between the hook and
`test_parameter_declarations.py`.

