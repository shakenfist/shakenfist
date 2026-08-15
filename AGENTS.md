# AGENTS.md - Guide for AI Coding Assistants

Conventions and gotchas for working on Shaken Fist that you cannot infer
by reading the code. Everything else is documented elsewhere; this file
points you there rather than restating it.

## Project context

Shaken Fist is a minimal cloud orchestration platform for VM and network
management, designed to be understood in its entirety by a single
developer. The component map is [ARCHITECTURE.md](ARCHITECTURE.md).

## Where the documentation lives

| Question | Document |
|----------|----------|
| What are the components, and how do they fit together? | [ARCHITECTURE.md](ARCHITECTURE.md) |
| What are the code conventions, and how do I test? | [docs/developer_guide/standards.md](docs/developer_guide/standards.md) |
| What rules exist because of a past bug? | [docs/developer_guide/coding_rules.md](docs/developer_guide/coding_rules.md) |
| How does CI work, what gates a PR, what bot commands exist? | [docs/developer_guide/ci.md](docs/developer_guide/ci.md) |
| How do I write an endpoint? | [docs/developer_guide/writing_an_endpoint.md](docs/developer_guide/writing_an_endpoint.md) |
| How does the database layer behave? | [docs/developer_guide/database_internals.md](docs/developer_guide/database_internals.md) |
| How are network operations dispatched? | [docs/developer_guide/network_dispatcher.md](docs/developer_guide/network_dispatcher.md) |
| How do the scheduler, the health surfaces, the daemon watchdog and REST contracts work? | [docs/developer_guide/subsystem_internals.md](docs/developer_guide/subsystem_internals.md) |
| What is the security model? | [docs/developer_guide/security_model.md](docs/developer_guide/security_model.md) |
| How does authentication work? | [docs/developer_guide/authentication.md](docs/developer_guide/authentication.md) |
| How do object state machines work? | [docs/developer_guide/state_machine.md](docs/developer_guide/state_machine.md) |
| How do I update the documentation? | [docs/developer_guide/updating_docs.md](docs/developer_guide/updating_docs.md) |

Anything not in that table is still in `docs/`. The complete list of
pages, in reading order, is the `nav:` section of
[mkdocs.yml](mkdocs.yml), rendered at
[shakenfist.com](https://shakenfist.com/).

## The rules that exist because we broke something

[docs/developer_guide/coding_rules.md](docs/developer_guide/coding_rules.md)
is not a style guide — every rule in it came out of a real defect. Read
it before touching authorisation predicates, parsers, lookup keys, or
metrics. The headlines:

- Never restate a visibility predicate; call the one that already exists.
- A check that runs after the parse is not a check.
- Two records must not claim one lookup key.
- Put the meter above the expensive thing, not below it.
- A guard has to sit where the exception is raised.
- Fail closed on a field, not on a formatting accident.
- Credential-carrying routes are not logged, not redacted.
- Cluster CI tests only run in the merge queue.

## Things that will bite you

- **Attribute updates use field masks.** Read
  [docs/developer_guide/standards.md](docs/developer_guide/standards.md)
  before writing an attribute update; a whole-object write races.

- **Protobuf enums are generated, not hand-written.**
  `shakenfist/schema/` is the source of truth. Add a member with the next
  available `proto_id`, run `tox -e genprotos`, and never change or reuse
  an existing `proto_id`.

- **Secret-carrying fields are `SecretStr`.** Stringifying one yields
  `**********`; unwrap with `.get_secret_value()` only at named
  boundaries. A `SecretStr` never compares equal to a `str`, and the
  obvious leak assertions pass vacuously — see
  [docs/developer_guide/standards.md](docs/developer_guide/standards.md#secret-carrying-fields-are-secretstr).

- **Native MariaDB ENUM columns freeze their value list at `CREATE
  TABLE` time.** Adding a Python enum member therefore works on a fresh
  install and breaks every existing database with "Data truncated for
  column" — which greenfield CI does not catch. You do not write a
  migration for it, because `_ensure_native_enum_columns()` reconciles;
  see
  [docs/developer_guide/standards.md](docs/developer_guide/standards.md#native-enum-columns-and-python-enums).

- **A long daemon pass that never reaches `idle()` must call
  `pet_watchdog()`.** systemd kills the process at `WatchdogSec`
  otherwise, while it is working normally. See
  [docs/developer_guide/subsystem_internals.md](docs/developer_guide/subsystem_internals.md#daemon-liveness-systemd-watchdog).

- **A new on-disk object type declares its `health_dependencies`**, or
  node health will not notice when the storage it lives on fails. See
  [docs/developer_guide/subsystem_internals.md](docs/developer_guide/subsystem_internals.md#node-resource-health).

- **In-memory only objects never touch the database**, and **API
  parameter declarations are enforced at import time** — both are easy to
  violate without a local failure. See
  [docs/developer_guide/standards.md](docs/developer_guide/standards.md).

- **Events and logs are different channels.** An event is a durable,
  queryable record on an object; a log line is not. See
  [docs/operator_guide/events.md](docs/operator_guide/events.md).

- **Capacity arithmetic lives in three places that must stay in sync**:
  `_compute_reservations()` in the resources daemon,
  `Scheduler._schedulable_threads()` / `_memory_reserved_mb()`, and the
  reconciler's limit-derivation helpers in `mariadb.py`. Changing one
  means changing all three — see
  [docs/developer_guide/subsystem_internals.md](docs/developer_guide/subsystem_internals.md).

- **Node error never clears automatically.** `sf-ctl clear-node-error` is
  the operator recovery path; see
  [docs/operator_guide/node_health.md](docs/operator_guide/node_health.md).

## Documentation

`docs/` is the home for anything a human would also want to read. This
file and `ARCHITECTURE.md` are a summary and an index into it: put new
detail in `docs/` and link to it from here rather than growing either
file. `docs/components/` is an automated import of the sibling
repositories' documentation — never edit it here, fix it at the source.

Conventions for editing the documentation, including the mkdocs nav, are
in [docs/developer_guide/updating_docs.md](docs/developer_guide/updating_docs.md).
