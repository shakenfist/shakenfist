Thanks for your work on this. I appreciate it. Some final checks
before I push:

## Code quality

 * Did the changes introduce any significant amount of duplicated
   code? Are there any missed opportunities for code reuse or
   refactoring?
 * Should any new code be extracted into a shared module? Look for
   logic that a second object type or daemon would likely need.
 * Are there any TODO comments we should address as part of this
   work?
 * Please ensure all source code is wrapped at 120 characters.
 * Use single quotes for strings, double quotes for docstrings.

## Style conformance

 * Does the code follow the project conventions in `CLAUDE.md`?
   Check in particular:
   - Python conventions (import ordering, logging pattern,
     copyright headers).
   - Object lifecycle conventions (state machine transitions,
     `hard_delete()` cleanup, event logging).
   - Database access conventions (three-layer direct/gRPC/public
     pattern in `mariadb.py`, Pydantic schemas in
     `shakenfist/schema/`).
   - gRPC conventions (proto definitions, stub generation via
     `tox -e genprotos`).

## Tests

 * Is there unit and functional test coverage for the changes?
   This should include normal and adversarial cases.
 * All tests should pass. We need to fix any failing tests now
   before we push.
 * What tests are skipped? Could we reduce that number?
 * Run `pre-commit run --all-files` and confirm all hooks pass
   (flake8, stestr, mypy).

## Documentation

 * Has `docs/` been updated to reflect any new or changed
   features? In particular, has `docs/operator_guide/database.md`
   been updated for any database schema changes?
 * Has `ARCHITECTURE.md` been updated if this change adds or
   modifies modules, daemons, or object types?
 * Has `README.md` been updated if usage instructions, project
   structure, or setup steps have changed?
 * Has `AGENTS.md` been updated?
 * Is all deferred work and pre-existing errors listed in a plan
   file?

## Security review

 * Review these changes as both a security reviewer and an
   experienced developer and correct any errors you find.
 * Are any user-controlled values (API inputs, namespace names,
   instance metadata) used in file paths, SQL queries, or shell
   commands without sanitization?
 * Do new gRPC or REST API endpoints enforce proper authentication
   and namespace authorization?

## Build verification

 * Does `pip install -e .` succeed?
 * Does `tox` pass?
 * If proto files were modified, were stubs regenerated with
   `tox -e genprotos`?
