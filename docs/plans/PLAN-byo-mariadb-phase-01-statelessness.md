# Phase 1: Statelessness fixes and compatibility check

Parent plan: [PLAN-byo-mariadb.md](PLAN-byo-mariadb.md).

## Prompt

Before responding, read these source files end-to-end —
they are the substrate this phase mutates:

- `shakenfist/mariadb.py` lines 440-500 (engine factory,
  metadata, schema_versions table getter), 2107-2178
  (`ensure_schema()`), 2180-2350 (`ensure_data_migrations()`
  and the surrounding DATA_MIGRATIONS framework), and a
  representative chunk of `_ensure_*_schema()` functions
  around 540-700 to understand the per-table version
  pattern.
- `shakenfist/daemons/database/main.py` lines 5070-5150
  (the daemon startup sequence that calls `ensure_schema()`
  and `ensure_data_migrations()`).
- `shakenfist/client/ctl.py` lines 199-235
  (the existing `sf-ctl ensure-mariadb-schema` command).
- `shakenfist/config.py` lines 488-520 (the MariaDB config
  block — read-only context; the `MARIADB_HOST=localhost`
  bootstrap conditional is **phase 2**, not phase 1).

This phase does *not* touch the `MARIADB_HOST` /
`DATABASE_NODE_IP` config-bootstrap rewrite, the gRPC LB
channel, or the deploy machinery. Those are phases 2-7.
Phase 1 changes only: (a) the schema_versions table getter,
(b) the daemon-startup verify-vs-run path, and (c)
`sf-ctl ensure-mariadb-schema` to gain a compatibility
gate before any schema work.

Phase 0 (etcd machinery deletion) is assumed landed by
the time this phase executes. If phase 0 has not yet
landed, the brief for step 3 (replacing the daemon-
startup calls) must account for the
`ensure_data_migrations()` call still being present and
must remove the call rather than just stop invoking it
from the daemon — phase 0 will then delete the function
body in its sweep.

One commit per step at minimum. Each commit must build,
pass `pre-commit run --all-files`, and have a clear
message.

## Context

The phase exists for two coupled reasons:

1. **Two specific blockers make `sf-database` unsafe to run
   as N>1 instances today.** Both are touched here:
   - The schema_versions table getter
     (`mariadb.py:476-492`) lacks the
     `TABLE_CREATION_LOCK` + double-check pattern that
     every other table getter (e.g.
     `_get_object_states_table` at line 556) uses.
     Concurrent `ensure_schema()` callers (either two
     `sf-ctl ensure-mariadb-schema` invocations on
     different operator control machines, or — pre-
     phase-3 — two sf-database starts) can race in
     SQLAlchemy's metadata registry.
   - `ensure_data_migrations()` is documented as
     non-concurrent (its own docstring at `mariadb.py:2230`
     says so). This is dissolved not by adding a lock
     but by removing migration execution from daemon
     startup entirely: `sf-ctl ensure-mariadb-schema`
     becomes the only path that runs migrations,
     consistent with the master plan's "operator is
     in charge" framing.

2. **Operator-provided MariaDB needs an explicit
   compatibility gate.** Today the deployer always
   provides a known MariaDB; once BYO becomes the
   default, operators may bring versions, engines, or
   character sets that SF cannot run against safely.
   Catching the mismatch at `sf-ctl ensure-mariadb-schema`
   time (and again at sf-database startup, in case the
   server gets swapped underneath) is much friendlier
   than the first JSON-column write failing at runtime.

After this phase lands, `sf-database` does no schema
work at startup. It performs a `verify_mariadb_compat()`
check (server version vs floor, default storage engine,
default charset/collation) and a
`verify_schema_versions()` check (every expected SF
table is at the version this build expects), and
refuses to start with an explicit error pointing the
operator at `sf-ctl ensure-mariadb-schema` if either
fails.

## Decisions (phase-local)

These resolve the master plan's open questions that
land in phase 1.

1. **MariaDB version floor: `10.6.0`.** Inputs to the
   choice:
   - SF uses `sa.JSON()` columns (extensive) and the
     `JSON_VALUE` / `JSON_CONTAINS` / `JSON_EXTRACT`
     functions (`mariadb.py:17215-17282`). All four
     are available from MariaDB **10.2**, so the JSON
     features themselves are not the binding
     constraint.
   - Cluster CI's functional tests run against
     `debian-12` which ships MariaDB **10.11**, so
     10.11 is the version we actually exercise.
   - Ubuntu 22.04 LTS (still supported through 2027)
     ships MariaDB **10.6**; Ubuntu 24.04 LTS and
     debian-12/13 ship **10.11**.
   - Pinning to 10.11 ("support what we test") locks
     out perfectly working 22.04 deployments without
     a concrete reason. Pinning to 10.6 gives
     comfortable headroom above the 10.2 features
     floor, supports every LTS currently in vendor
     support, and asks the operator on 10.5-or-older
     to upgrade something that has been EOL since
     2024-06-24.
   - Adopting 10.6 commits us to keeping it working;
     a single CI matrix entry exercising 10.6 against
     unit-tests is the cheapest insurance and is
     filed as future work below.

   *Addendum (2026-08-02): superseded -- the floor is
   now `10.11.0`. The `ipam_reservations.address`
   INET4 column (which post-dates this decision)
   requires MariaDB 10.10+, so the 10.6 floor had
   silently stopped being achievable:
   `verify_mariadb_compat` passed servers on which
   `ensure-mariadb-schema` could not actually run.
   Found by scheduler-reservations phase 2 validation;
   10.11 chosen as the oldest in-support LTS above
   10.10, and the version CI actually exercises.*

2. **Compatibility check is a hard refuse, not a
   warning.** Per master plan decision 13. Implication:
   the check raises a typed exception
   (`MariaDBIncompatibleError` is a reasonable name)
   that the callers convert to a `click.ClickException`
   in `sf-ctl` and a `SystemExit(1)` in
   `daemons/database/main.py`. No `--force` override.
   An operator who genuinely wants to run an
   unsupported server can patch the constant in their
   fork; SF does not ship a way to ignore the gate.

3. **Schema-version check uses an explicit expected-
   versions map, not the live `_ensure_*_schema`
   functions.** Today each `_ensure_*_schema()`
   function knows its own target version internally
   (as a function-local constant or argument). Phase 1
   extracts those into a single module-level dict
   (`EXPECTED_SCHEMA_VERSIONS: dict[str, int]`) that
   both `ensure_schema()` and the new
   `verify_schema_versions()` helper consult. This
   avoids `verify_schema_versions()` having to invoke
   the `_ensure_*_schema()` functions just to read
   their version constants, keeps the verification
   path side-effect-free, and gives a single place to
   bump versions on schema changes. The extraction is
   mechanical but touches all 36 tables; the brief
   for step 2 calls this out explicitly.

4. **Check covers server version, default engine,
   default charset, default collation. No feature-flag
   introspection.** Per master plan decision 13.
   Specifically:
   - **Version**: `SELECT VERSION()` returns a string
     like `10.11.5-MariaDB-1:10.11.5+maria~ubu2204`.
     Parse the leading `M.m.p` and compare against
     `MIN_MARIADB_VERSION = (10, 6, 0)`. Refuse if
     either older or — defensively — if the string
     does not contain `MariaDB` (operator pointed us
     at upstream MySQL, which we do not support).
   - **Default storage engine**: `SELECT
     @@default_storage_engine` must be `InnoDB`.
   - **Connection-default character set**: `SELECT
     @@character_set_database` must be `utf8mb4`.
   - **Connection-default collation**: `SELECT
     @@collation_database` must start with
     `utf8mb4_`. The specific collation suffix is
     not enforced; operators may prefer `unicode_ci`,
     `unicode_520_ci`, or others.

5. **The compat check runs from
   `sf-ctl ensure-mariadb-schema` before any schema
   work AND from `sf-database` startup before serving
   any request.** Both call the same
   `verify_mariadb_compat()` helper. Cost is one
   round-trip; not a concern.

6. **Behaviour when `MARIADB_HOST` is unset is
   unchanged in this phase.** Both `ensure_schema()`
   today and the new verify helpers raise
   `RuntimeError` if `MARIADB_HOST` is unset; that
   stays. Phase 2 owns the `MARIADB_HOST` plumbing
   rework; phase 1 lives inside the existing
   assumptions.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | low | sonnet | none | Wrap `_get_schema_versions_table()` (`shakenfist/mariadb.py:476-492`) in `with TABLE_CREATION_LOCK:` using the same double-check pattern as `_get_object_states_table()` at lines 556-589. Specifically: inside `if _schema_versions_table is None:`, acquire `TABLE_CREATION_LOCK`, re-check `if _schema_versions_table is not None: return _schema_versions_table`, then check `if 'schema_versions' in metadata.tables: _schema_versions_table = metadata.tables['schema_versions']; return _schema_versions_table`, then construct as today. Add a regression test in `shakenfist/tests/test_mariadb.py` (or a new file `test_mariadb_schema_concurrency.py`) that spawns two threads each calling `_get_schema_versions_table()` and asserts no exception and identical returned object. One commit. |
| 2 | medium | opus | none | Extract per-table target versions into a single module-level dict `EXPECTED_SCHEMA_VERSIONS: dict[str, int]` in `shakenfist/mariadb.py` near the existing schema-version helpers (after `_set_table_version`, before `_ensure_schema_versions_table`). Survey every `_ensure_*_schema()` function (there are 36 of them, listed by `ensure_schema()` at lines 2133-2168) and lift each one's target-version constant out into the new dict, keyed by table name. Update each `_ensure_*_schema()` to read from `EXPECTED_SCHEMA_VERSIONS[<table_name>]` rather than carry its own constant. The constants today look like `OBJECT_STATES_VERSION = 3` (or similar) defined at module scope or function scope; sweep both. Verify with `pre-commit run --all-files` that nothing else imports those constants directly (if any do, update the import to read from the dict). One commit. |
| 3 | high | opus | worktree | Add two new helpers in `shakenfist/mariadb.py`: (a) `verify_mariadb_compat(engine: sa.Engine) -> None` raises a new typed exception `MariaDBIncompatibleError` on mismatch, per phase 1 decision 4. Implement the four checks (version `>= 10.6.0` and string contains `MariaDB`, `@@default_storage_engine == 'InnoDB'`, `@@character_set_database == 'utf8mb4'`, `@@collation_database` prefix `'utf8mb4_'`). Each failure adds a line to the exception message; raise once at end if any failed so the operator sees all mismatches in one error. Define `MIN_MARIADB_VERSION = (10, 6, 0)` at module top alongside the other constants. (b) `verify_schema_versions(engine: sa.Engine) -> None` reads `schema_versions` and compares every entry against `EXPECTED_SCHEMA_VERSIONS`; raises `SchemaVersionMismatchError` listing every mismatched table with both expected and actual versions. Tables in `EXPECTED_SCHEMA_VERSIONS` with no row in `schema_versions` count as a mismatch (actual=0). Wire both into `sf-ctl ensure-mariadb-schema` in `shakenfist/client/ctl.py:206-234`: call `verify_mariadb_compat(engine)` immediately after acquiring the engine, before `mariadb.ensure_schema()`. Convert the typed exception to `click.ClickException`. (Do not call `verify_schema_versions()` from `sf-ctl ensure-mariadb-schema` — the *point* of that command is to bring the schema to the expected version, so checking-before-running would be circular. The version check belongs at daemon startup only.) Add `MariaDBIncompatibleError` and `SchemaVersionMismatchError` to `shakenfist/exceptions.py` if there is a natural section for typed mariadb errors, otherwise define them at the top of `mariadb.py`. Use worktree isolation: the brief touches three files in non-obvious ways and is worth being able to discard if the output is unsatisfactory. One commit. |
| 4 | high | opus | worktree | In `shakenfist/daemons/database/main.py` lines 5142-5146, replace `mariadb.ensure_schema()` and `mariadb.ensure_data_migrations()` calls with `mariadb.verify_mariadb_compat(mariadb._get_engine())` and `mariadb.verify_schema_versions(mariadb._get_engine())`. Both helpers raise on mismatch; let them propagate to the existing top-level exception handling in `main()` (the `if not config.MARIADB_HOST` block immediately above already uses `raise SystemExit(1)`, so the same exit pattern applies — wrap the verify calls in a try/except that logs the exception's message at error level and raises `SystemExit(1)`, so operators see the human-readable error rather than a traceback). The log message must explicitly tell the operator to run `sf-ctl ensure-mariadb-schema` to address a schema-version mismatch, and to fix the MariaDB server / config to address a compatibility mismatch. Worktree isolation: changing daemon startup is the highest-blast-radius change in this phase; getting it wrong means daemons stop starting. One commit. |
| 5 | medium | sonnet | none | Unit tests in `shakenfist/tests/test_mariadb_compat.py` (new file). Cover: version-parse helper handles `10.6.0-MariaDB`, `10.11.5-MariaDB-1:10.11.5+maria~ubu2204`, `10.5.99-MariaDB`, `8.0.35` (MySQL), and a garbage string; the four compat checks each individually pass and individually fail; `verify_schema_versions()` reports every mismatched table in a single exception; `verify_schema_versions()` treats a table missing from `schema_versions` as actual=0. Mock the engine/connection using the existing `mock.patch` patterns from `shakenfist/tests/test_mariadb*.py`. One commit. |
| 6 | medium | sonnet | none | Documentation. Update `docs/operator_guide/database.md` to add a "MariaDB compatibility requirements" section near the top of the BYO section (which doesn't exist as a coherent section yet — that's phase 7's job; just slot the new content in as a top-level subsection for now and let phase 7 reorganise). Cover: minimum version 10.6.0, MariaDB-not-MySQL, InnoDB engine, utf8mb4 charset, utf8mb4_* collation, the operator workflow "ensure-mariadb-schema runs the check before doing any schema work; sf-database runs the same check at startup." Update `docs/operator_guide/upgrades.md:45-57` which describes `ensure-mariadb-schema` today to mention the new compat check. No `README.md` / `ARCHITECTURE.md` / `AGENTS.md` updates in this phase — those are phase 7. One commit. |

## Validation

- `pre-commit run --all-files` passes after each step.
- A locally-spun-up MariaDB 10.6 docker container
  (`docker run --rm -d -e MARIADB_ROOT_PASSWORD=test
  -p 3306:3306 mariadb:10.6`) accepts both
  `sf-ctl ensure-mariadb-schema` and a subsequent
  `sf-database` start. A 10.5 container is refused
  by both with a clear error.
- A MySQL 8.0 container (operator pointed us at the
  wrong server) is refused.
- Concurrent threads calling
  `_get_schema_versions_table()` return the same
  object and raise no exception (covered by the
  regression test in step 1).
- A test deployment where the operator forgot to run
  `sf-ctl ensure-mariadb-schema` after a SF version
  bump produces a clear "schema_versions out of date,
  run ensure-mariadb-schema" error from sf-database
  startup, not a runtime SQL error somewhere later.

## Risks

- **The schema-version map extraction (step 2) touches
  every `_ensure_*_schema()` function in
  `mariadb.py`.** That is 36 functions across a
  thousand-line span. The risk is a typo in one of the
  table-name strings causing a silent
  `EXPECTED_SCHEMA_VERSIONS` miss. Mitigation: the
  step's brief tells the sub-agent to verify the dict
  against the list of `_ensure_*_schema()` calls in
  `ensure_schema()` (lines 2133-2168) and assert one-
  to-one correspondence in a unit test.
- **`verify_schema_versions()` on an empty database
  (no `schema_versions` table) raises a different
  error today** — `_get_table_version()` returns -1
  per its docstring. The brief for step 3 must
  handle "schema_versions table does not exist" as
  a distinct mismatch (every expected table is
  effectively at version 0 / missing), with an error
  message that says "MariaDB schema has not been
  initialised; run `sf-ctl ensure-mariadb-schema`."
- **Removing `ensure_data_migrations()` calls
  pre-emptively in step 4 (before phase 0 deletes
  the function body) leaves the function in tree
  with no callers.** This is harmless: phase 0 will
  delete it in its sweep. The brief for step 4
  notes this.
- **Operators on Ubuntu 22.04 (MariaDB 10.6) are
  inside the floor by a literal point release.**
  If we discover during validation that some
  10.6.x patch level is needed for a specific JSON-
  function behaviour, bumping the floor by a patch
  version is cheap. The choice of 10.6.0 (not
  10.6.5 or similar) is intentional: "the major.
  minor we test against, no patch-level
  superstition."

## Back brief

Before executing this phase, please back brief the
operator on:

- The five concrete code changes (schema_versions
  lock; EXPECTED_SCHEMA_VERSIONS dict; the two
  verify helpers; the daemon-startup swap; the
  ensure-mariadb-schema compat call) and which files
  carry each change.
- The pinned version floor (`10.6.0`) and the
  reasoning so it can be re-litigated if the
  operator disagrees with the trade-off.
- The deliberate decision to *not* touch
  `MARIADB_HOST` plumbing in this phase (that is
  phase 2) and the fact that phase 0 is assumed to
  have landed (or the step 4 brief needs an
  adjustment).
