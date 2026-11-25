Concepts and Standards
===
# Ensuring a Common Language within the code base

This document records the standards and common language used within the Shaken Fist software system.

It should also record why the choice was made.

(This is actually just notes to save our future selves from tripping over the same problems.)

## Development goals

These apply to every change, and a reviewer will ask about each:

- An object's `hard_delete()` cleans up everything the object owns. A new
  table, reference row or on-disk artifact is not finished until
  `hard_delete()` removes it.
- New code carries mypy annotations, and a module a change touches is a
  candidate to join the incremental rollout; see [mypy.md](mypy.md).
- New code has tests. Functional tests (in
  `shakenfist/deploy/shakenfist_ci/`) are preferred over unit tests where
  only one is practical, because they exercise the real daemons and
  database.
- Event coverage is preserved. The wording of an event may change, but a
  change must not remove the audit trail an operator debugs from; see
  [Events vs logs](#events-vs-logs).
- Filtering is pushed down into SQL, and a new query arrives with the index
  that serves it; see
  [SQL Filter-Pushdown Discipline](database_internals.md#sql-filter-pushdown-discipline).

## Memory

Memory is measured in MiB in Shaken Fist. All references to memory size are stored and transmitted in MiB: Gigabytes can be too big if you want a lot of small machines. Kilobytes is just too many numbers to type. The ```libvirt``` API measures memory in KiB. Therefore, interactions with the library need to be careful to convert from MiB to KiB.

## Code Style

- Single quotes for strings, double quotes for docstrings
- Never triple single quotes; triple double quotes instead
- 120 character line wrap
- Trim trailing whitespace
- All imports at the top of the file. A late import inside a function is
  only for breaking a circular import, and carries a comment saying so
- Import order is standard library, third party, then `shakenfist`. The
  `shakenfist_utilities` import carries a `# noreorder` marker to keep it
  in that position
- Every module sets up logging with `LOG, _ = logs.setup(__name__)` (from
  `shakenfist_utilities`), and attaches structured context with
  `LOG.with_fields({'instance_uuid': uuid})` rather than formatting it into
  the message
- Every file starts with `# Copyright 2019 Michael Still and contributors`
- Comments, docstrings, test names and log messages describe the code as
  it is, not the plan that built it: no `added in phase 5`,
  `per decision 3` or `pending step 5f`. Write the reason the citation
  stood for. The rule is the `plan-references-in-code` block in
  `PUSH-AUDIT.md`, the `check-plan-phase-references` pre-commit hook
  rejects the four numbered shapes in documentation, source and
  configuration at commit time -- including a citation split across a
  line wrap, which the audit's line-at-a-time reading cannot see -- and
  the daily
  [plan history in source](../components/development/audits/plan-history-in-source.md)
  audit measures what is left out of band. A line where the shape is
  genuinely not a plan reference carries `# audit-ok: phase-reference`;
  a file made of the shapes, such as a test suite for a plan-reference
  checker, carries `# audit-ok: phase-reference-file` once near the top,
  with a sentence saying why
- Two more citation shapes are reported in Python without failing the
  commit: a numbered part of a plan cited by its bare label and a
  pointer to a `PLAN-*.md` file. A label is a letter and one or two
  digits -- `D13` for a master plan's decision, `P7` where a phase plan
  numbers its own decisions, `F5` or `N1` for a survey's findings and
  its census -- matched case-sensitively, because `p90` is a percentile,
  and at two digits at most, because `# noqa: F401` cites nothing. The
  same hook finds them, with a `tokenize` walk over comments and string
  literals and an `ast` walk over `def` and `class` names, so that `D13`
  is read as a citation in prose and as somebody's variable in code.
  Several hundred are in the tree, which is why they are advisory: fix
  the ones in the files you touch, and run
  `python3 tools/check-plan-phase-references.py --advisory` to see all of
  them. Two survive the rule and carry
  `# audit-ok: plan-reference` on the line, or
  `# audit-ok: plan-reference-file` once near the top of a file -- a
  pointer to work that is not built yet, where an issue link is better
  if one exists, and a string persisted as data, where rewording it
  splits a committed dataset on one condition. Those are the tokens the
  fleet's
  [plan references in source](../components/development/audits/plan-source-references.md)
  audit accepts, so one marker answers both
- A `PLAN-*.md` pointer which survives has to land on a file, which is
  not advisory: nothing renders one -- a path in a comment or a YAML
  value is inert text no link checker follows -- so a renamed or
  archived plan rots it in silence. The same hook fails the commit for
  a pointer in a tracked non-markdown file which resolves neither from
  the repository root nor from `docs/`, and not bare against the plans
  in `docs/plans/` at any depth. A plan in another repository is
  written as an absolute `https://github.com/.../docs/plans/PLAN-x.md`
  URL, which is not resolved here, and the two markers above exempt a
  line or a file of fixtures -- which is how the test suites for this
  tooling name plans that deliberately do not exist

This page is the style guide; `AGENTS.md` summarises it and links here.

## Attribute updates use field masks

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

**A per-attribute lock is not a substitute, and this is the part which
surprises people.** The locks are per attribute while the write is whole
row, so two correctly locked writers clobber each other: `add_instance()`
held the `instances` lock and `register_daemon()` held `daemons`, and
either one, saving a snapshot loaded before the other committed, reverted
it. Unlocked writers make it worse rather than being the whole problem.
`Node.observe_this_node()` runs every 15 seconds from both sentinel
daemons on every node, and the resources daemon's version setters run
every 300 seconds -- with `dependency_versions` saving a snapshot taken
before a `dpkg-query` subprocess ran, which widens the stale window to
seconds. The symptom that found this was a scheduler affinity pass
scoring every node zero, because the instance it was affine to had
vanished from its node's `instances` list about 55 seconds after
placement wrote it there. A mask is the fix because it removes the
stale columns from the statement entirely; a bigger lock would only
serialise writers which have no business contending.

The mask is validated in the public `update_*_attributes` function,
before it dispatches to the direct or gRPC implementation, so an unknown
field name raises `ValueError` either way. Validating only in the direct
path would have made the same mistake a silently discarded `StatusReply`
failure over gRPC.

**During a rolling upgrade the mask degrades rather than failing.** An
`sf-database` which has not restarted yet does not know the `fields`
proto field, ignores it, and writes the whole row -- exactly the
previous release's semantics, until the daemon rolls. This is accepted
without a guard, as it was when the instance, artifact and network masks
shipped: the window is one deploy long and the failure it reintroduces is
the one which existed before.

Two multi-writer lists are still packed into attribute rows, both
deliberately. `namespace_attributes.trust` is small and written rarely,
so naming it in the mask is enough and un-packing it would buy nothing.
`node_attributes.daemons` is redundant rather than tolerable -- the
registration set is derivable from the `node_daemon_states` rows which
`register_daemon()` and `deregister_daemon()` already maintain -- and
removing it is tracked as
[#5060](https://github.com/shakenfist/shakenfist/issues/5060). A blob's
`last_used` is advisory, so a full-row blob write reverting a concurrent
`update_blob_last_used()` costs nothing worth guarding.

## A guarded UPDATE must be the transaction's first statement

`innodb_snapshot_isolation` defaults ON from MariaDB 11.6.2, which is
what Debian 13 and every recent container tag ship (Ubuntu 24.04 is
still on 10.11, where it defaults OFF). Under it, a REPEATABLE READ
transaction whose read view was established by a plain `SELECT` does
not block and re-evaluate a later `UPDATE` whose target row has moved
since — it aborts the whole transaction with ER_CHECKREAD (1020). When
the guarded `UPDATE` is instead the transaction's *first* statement, the
read view is established by the DML itself, there is no stale-snapshot
window, and a contending writer
blocks on the row lock and then re-evaluates its `WHERE`, which is the
behaviour every guarded-counter design in `shakenfist/mariadb.py`
depends on.

So in `_direct_admit_instance_placement()` and
`_direct_release_instance_placement()` the branch selects and presence
probes run on a separate autocommit connection *before* `engine.begin()`
(`_probe_admission_rows()`, `_probe_release_rows()`). Reads that happen
*after* the transaction's own writes are fine, because those rows are
already locked by them, and a subquery carried inside a guarded `UPDATE`
is fine because it is part of the DML that establishes the read view.
Moving a probe back inside the transaction re-introduces the failure: it
was measured as 46 of 50 concurrent admissions exhausting the retry
budget and turning an instance create into an HTTP 500.

The structural regression tests are `SnapshotIsolationInvariantTestCase`
in `shakenfist/tests/test_mariadb_capacity_admission.py`; the
behavioural one is `PlacementAdmissionConcurrencyLiveTestCase` in the
matching `_live` module, which only bites against a server with the
variable ON. The `schema_enum_widening` job runs the live modules on a
`debian-13` runner against Debian 13's MariaDB 11.8, where it is ON, so
CI does enforce this in the merge queue — but not on a pull request,
where that job does not run. See
`docs/plans/PLAN-scheduler-reservations-phase-03-primitive.md` step 6a.

## Secret-carrying fields are `SecretStr`

Fields which hold a credential — `key` and `nonce` on
`NamespaceKeyAttributesData`, `token` on `BlobTransfer`, and the
`AUTH_SECRET_SEED`, `MARIADB_PASSWORD` and `LOKI_AUTH_HEADER`
configuration options — are
`pydantic.SecretStr`, so stringifying one yields `**********` rather
than the value. Unwrapping with `.get_secret_value()` happens only at
named boundaries (the SQL writes, the gRPC encoders, the bcrypt
compare, the JWT claim, and the transfer handshake the transfers daemon
compares against), and each of those sites carries a comment
saying what breaks if the unwrap is missed. Do not add an unwrap to
make an error go away; move the value, or wrap the other side.

One credential is a plain `str` on purpose: the secret
`shakenfist/util/credentials.py`'s `generate()` mints for a new key. It
is the one value in the system which is a bearer credential rather
than a hash of one, but its whole job is to reach the HTTP response
body, and the response path has no typed serialiser where an unwrap
could be made structural. A missed unwrap there would not fail loudly:
it would render `**********` into the operator's only copy of the
credential. Revisit this if the response path gains such a
serialiser, not before.

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

## Native ENUM columns and Python enums

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

## An import needs a declaration, not a transitive pin

Everything we import is declared in `pyproject.toml` *above* the
`# START_OF_INDIRECT_DEPS` marker. The block below that marker is
regenerated by `tools/pin-indirect-dependencies.sh` from whatever the
direct dependencies resolve to, so a package named only there is
installed by coincidence: the day the intermediate library stops
requiring it, the reconciler drops the pin and the import fails on a
branch which changed nothing. The CI harness imported `oslo_concurrency`
for years on `shakenfist-utilities`' declaration, which
`shakenfist-utilities` has since removed.

A test-only import counts. `annotated_types` reached the schema tests
as pydantic's requirement, which is a floor pydantic is free to move or
drop, so it is declared here as well (issue 5038).

Declare the package at the version it currently resolves to and delete
the generated copy; the reconciler leaves out anything already declared
above the marker, so the two never fight over one name — two pins for
one package break resolution as soon as Renovate bumps one of them
(issues 3398, 3399 and 3462). `shakenfist/tests/test_dependency_declarations.py`
asserts both halves of that, and the fleet-wide criterion is
[the undeclared direct dependency audit](../components/development/audits/undeclared-direct-dependency.md).

A dependency which is installed but genuinely never imported — a
command line tool, a gunicorn worker class — carries a
`# not-imported: <name> -- <reason>` comment instead.

## Documentation

- When a change adds, renames, or removes a user-visible concept
  (an object type, state, term, or similar), update
  [`glossary.md`](../glossary.md) in the same change so the
  glossary never drifts from the code.

## In-memory only objects never touch the database

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

## API parameter declarations are enforced at import time

Every endpoint handler declares its parameters in
`swag_from(api_base.swagger_helper(...))`, and `swagger_helper()`
validates each declaration as the module is imported — so a malformed
one raises `InvalidAPIDeclaration` and **sf-api will not start**. The
rules: every handler carries a declaration (an empty parameter list is
valid, no declaration at all is not); `location` is one of
`SWAGGER_PARAMETER_LOCATIONS` *and* is where the parameter actually
arrives (route segment → `path`, a parameter parsed by webargs or read
from `flask.request.args` → `query`, otherwise `body`); a `path`
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

A response is a `(httpcode, description, sample)` tuple plus an
optional fourth element, today `{'headers': [...]}`, each header a
`(name, type token, description)` triple from the same vocabulary. A
query-string parameter is parsed with
`@api_base.use_declared_args(PARAMETERS, defaults)`, which builds the
webargs schema from the same declaration list rather than from a
hand-authored one beside it.

`shakenfist/external_api/declarations.py` derives the correct answer
from the source and is shared by the fixer
(`tools/fix-api-parameter-locations.py --apply`), the pre-commit hook
and `test_parameter_declarations.py`; `test_openapi_spec.py` validates
the generated specification itself in CI. Full reference in
`docs/developer_guide/writing_an_endpoint.md`.

## Events vs logs

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

## Testing

```bash
tox                              # Run all tests
tox -eflake8                     # Lint check
tox -emypy                       # Type checking
tox -ecover                      # Coverage report
stestr run {test_name}           # Run specific test
```

Unit tests use `stestr` with `testtools`; the base class is
`ShakenFistTestCase` in `shakenfist/tests/base.py`. MariaDB and other
external dependencies are replaced with `mock.patch`
(`shakenfist/tests/mock_mariadb.py` provides the common database fakes).
Tests live in `shakenfist/tests/` as `test_<subject>.py`, with
subdirectories for `external_api`, `operations` and `schema`.

The ansible collection's modules are loaded from source by
`shakenfist/tests/ansible_module_loader.py`, which also owns the two
exceptions a stubbed `exit_json()` and `fail_json()` raise:
`ModuleExited` and `ModuleFailed`. Raise those rather than defining a
local pair, and in particular do not raise a `SystemExit` subclass to
mirror what the real `AnsibleModule` does. A `SystemExit` is not an
`Exception`, so a module which took the other outcome than the one a
test named escaped both `assertRaises()` and testtools and killed the
stestr worker, dropping that worker's remaining tests from the totals
and reporting a mismatch as a crash rather than as one failed
assertion. Ordinary exceptions are only safe there while no collection
module makes a terminal call inside a `try` which catches `Exception`,
which `shakenfist/tests/test_ansible_module_loader.py` enforces.

### A test run is bounded, and growth is attributed

`OS_TEST_TIMEOUT` bounds how long one test may take. Nothing used to
bound how much a worker may *allocate*, and a stestr worker runs many
tests in one long-lived process -- so a test which leaked still passed,
and the worker carried the leak into every test scheduled after it. One
reached 16.3 GB resident plus 5.9 GB of swap and held a workstation in
direct reclaim for an hour before it was killed by hand (issue #4928).

Two things now stop that, and both run by default:

- `tools/bounded-test-run.sh` wraps every `stestr run` in `tox.ini`. It
  prefers a per-run cgroup (`systemd-run --user --scope`, which bounds
  swap as well as resident memory and kills only that scope) and falls
  back to `RLIMIT_DATA` where there is no user systemd session, as on a
  CI runner. `RLIMIT_AS` is deliberately not used: CPython reserves far
  more address space than it touches. Both defaults are sized per
  worker, because the legitimate footprint is -- a worker of this suite
  peaks a little over 200 MB and stestr runs one per CPU -- so the
  scope's bound scales with the CPU count while `RLIMIT_DATA`, being
  per process, bounds a worker and says nothing about their sum.
  Override with `SF_TEST_MEMORY_MAX`, `SF_TEST_SWAP_MAX` and
  `SF_TEST_HEAP_MAX_MB`, any of them `none` to go without.
- `ShakenFistTestCase` samples `/proc/self/statm` after each test's
  cleanups have run, and fails that test if it added more than
  `SF_TEST_MEMORY_GROWTH_MB` megabytes, or if the worker is now over
  `SF_TEST_MEMORY_CEILING_MB`. The ceiling is what catches a leak
  spread thinly across many tests, and the test it names may be a
  victim rather than the culprit -- which is still a point in the run
  and a readable message rather than a `MemoryError` from inside
  whichever library allocated next. Both defaults live in
  `shakenfist/tests/memory.py`, not in `tox.ini`, so a script which
  runs stestr outside tox is still guarded, and both are loose:
  importing a module and parsing the tree are charged to whichever test
  first did it, so the worst-behaved test in the suite grows a worker
  by 63 MB without leaking anything. These catch a runaway; they are
  not a budget. Set either to `none` to switch it off.

A test which genuinely needs a large allocation should release it before
it returns, rather than holding it on the test instance -- the instance
is still alive when the sample is taken.

### A test's log output is captured, and reported only if it fails

`tox.ini` sets `SHAKENFIST_LOG_TO_STDOUT=1`, so
`shakenfist_utilities.logs.setup()` installs a
`logging.StreamHandler(sys.stdout)` on each module's logger. That runs
at import time, so the handler holds the process's real stdout -- which
in a stestr worker is also the subunit result stream. Every record a
test emitted therefore went into that stream, out through the stestr
parent's console and tox, and into the CI log: 82 MB for one run, and a
test which logged heavily ran no faster than that console could be
drained. One scheduler test took between 37 and 165 seconds on CI for
1.2 seconds of work, and the `Sanity checks` job's runtime swung between
6.5 and 10+ minutes with no code change behind it (issue #4991).

`ShakenFistTestCase` now points every such handler -- stdout and
stderr -- at an in-memory buffer for the duration of each test,
restores the real stream afterwards, and attaches what was captured as a
`log` detail only when the test did not pass. Details are transmitted on
success as well, so attaching it unconditionally would send the same
volume back the other way. `shakenfist/tests/logcapture.py` owns the
mechanism.

Three things follow for a test author:

- **A test must not give an already-handled logger a second console
  handler.** The capture redirects the handlers which existed when the
  test started, so one added during it bypasses the capture, and one
  added in a `setUp()` without a matching cleanup is inherited by every
  test the worker runs after it -- each of which adds another. 45 lines
  of `app.logger.addHandler(logging.StreamHandler(sys.stdout))` across
  the API tests were doing exactly that: 104 tests in `test_auth.py`
  emitted 8,939 copies of a few hundred records. The base class fails
  the test which adds one. Register a cleanup that removes it:
  `self.preserve_log_handlers('gunicorn.error')` restores a named
  logger's handler list, and `logcapture.forget_logger()` drops the
  handlers something else installed -- `Daemon.__init__()` calls
  `logs.setup()`, for instance. The root logger is restored for every
  test already, because the usual way to disturb it is not a deliberate
  act: importing `shakenfist/client/ctl.py` calls
  `logging.basicConfig()`, and the suite emitted 33,000 lines of stderr
  that way. A logger which had *no* handler gaining its first one is not
  reported: that is a module imported for the first time inside a test
  calling `logs.setup()`, and failing whichever test the worker happened
  to import it in would be a guard that depends on the suite's
  partitioning.
- **Nothing may hold a reference to the test instance after the test.**
  testtools keeps a test's `addOnException()` handlers for the life of
  the test object, so a bound method registered there leaves the
  instance in a reference cycle and everything it allocated resident
  until the cycle collector runs -- which the memory bound above then
  charges to a later test. The capture's own handler is therefore a
  closure over a list, not a method; see `logcapture.note_failure()`.
- **Assert on log output with a handler of your own**, not by reading
  stdout. `shakenfist/tests/external_api/test_request_logging.py` shows
  the shape: a `logging.Handler` subclass which collects records, added
  to the logger under test and removed on cleanup.

## Pre-commit Hooks

The repository uses pre-commit hooks to validate code before commits:

```bash
pip install pre-commit           # Install pre-commit
pre-commit install               # Set up git hooks
pre-commit run --all-files       # Run all hooks manually
```

Current hooks:
- `actionlint` - Validates GitHub Actions workflow files. It is not a
  Python package: install it with `brew install actionlint` on macOS, or
  from the [actionlint releases](https://github.com/rhysd/actionlint/releases)
  on Linux
- `ansible-lint` - Validates the `shakenfist.shakenfist` Ansible collection
  (`shakenfist/deploy/collection/`)
- `test-internal-ca` - Issues and renews certificates with the
  `internal_ca` role, without root, by running `tools/ci-test-internal-ca.sh`.
  It needs `ansible-core`, `certtool` (the `gnutls-bin` package) and
  `openssl`, and only runs when the role or the script changes. The
  sanity-checks workflow runs the same script
- `test-kerbside-cluster-config` - Drives the node role's `kerbside_preflight`
  and `kerbside_credentials` entry points against a stub `sf-ctl`, and renders
  the role's config template, without root, by running
  `tools/ci-test-kerbside-cluster-config.sh`. It needs only `ansible-core`,
  and only runs when those entry points, the role's `argument_specs`,
  defaults or config template, `examples/_shared/site.yml` (whose wiring of
  those entry points it checks structurally), or the script change. The sanity-checks
  workflow runs the same script
- `test-kerbside-wiring` - Checks how `examples/_shared/site.yml` and the node
  role's `kerbside_source` entry point wire in the `kerbside` role of the
  `shakenfist.kerbside` collection (which is tested in the Kerbside repository),
  without root, by running `tools/ci-test-kerbside-wiring.sh`. It installs the
  release `examples/_shared/requirements.yml` pins from Ansible Galaxy, so it
  needs network access to Galaxy, as well as `ansible-core`, `openssl` and
  `certtool` (the `gnutls-bin` package). It only runs when `site.yml`,
  `requirements.yml`, the `kerbside_source` entry point, the node role's
  `argument_specs` or defaults, the `internal_ca` role or the script change. The
  sanity-checks workflow runs the same script
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
