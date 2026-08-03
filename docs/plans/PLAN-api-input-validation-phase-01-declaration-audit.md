# Phase 1: Declaration audit

## Context

**Status: complete.**

This is phase 1 of
[`PLAN-api-input-validation.md`](PLAN-api-input-validation.md).

Phase 0 decided to compile the `swagger_helper()` parameter
declarations into validation schemas rather than author new ones,
on the strength of a measurement: 97% of declared parameter names
match a kwarg their handler actually accepts. It then measured
the *locations* and found the opposite — **116 of the 119
parameters that appear in a URL path are declared as something
other than `path`.**

Phase 1 closes that gap. It is a precondition for phase 3: a
parser uses the declared location to decide where to look for a
value, so compiling today would look for `artifact_ref` in the
query string of a request that carries it in the path.

It is also worth landing on its own merits, independently of
whether the rest of this plan ever happens. The published
OpenAPI at openapi.shakenfist.com currently documents almost
every path parameter as a query parameter, and tells callers to
send `sshkey` and `userdata` to create an instance when the
handler reads `ssh_key` and `user_data`. Anyone generating a
client from that specification gets a broken client.

**No behaviour changes.** Nothing in this phase reads the
declarations at runtime; they are still documentation-only when
it ends. What changes is that they become *true*, and a test
keeps them that way.

## Ground truth

The correction is mechanical rather than a matter of judgement,
because two things in the tree are authoritative:

* **`app.py`'s `add_resource()` calls** say exactly which
  parameter names appear in a URL path, for each endpoint class.
  Any declared name matching a `<param>` in a mounted route is a
  path parameter, whatever it currently claims to be.
* **The handler signature** says exactly which names the endpoint
  can receive. A declared name that is not a signature kwarg is
  either drift or a pseudo-parameter.

Phase 1 does not ask a human to decide any of the 116; it derives
them and then pins the derivation with a test.

## Worklist

### W1 — Path parameter locations (116 declarations)

Rewrite the location token to `'path'` for every declared
parameter whose name appears in a `<...>` segment of a route the
class is mounted on. Current state:

| Declared as | Count |
|---|---|
| `query` | 104 |
| `body` | 11 |
| `qeury` (typo) | 1 |
| `path` (already correct) | 3 |

The 11 declared `body` deserve a look rather than a blind
rewrite: a body key of the same name currently *does* reach the
handler and override the path segment, so somebody may have
written `body` deliberately. Phase 0 decided (D8) that the
override is a hazard rather than a feature — `log_request`
already dodges one instance of it by mapping a body `uuid` to
`passed_uuid` — so the expected outcome is that all 11 become
`path`. Record any that look intentional instead of silently
converting them.

### W2 — Invalid location tokens (2)

* `artifact.py:641` — `('max_versions', 'post', ...)`. `post` is
  not an OpenAPI location. This is a body parameter.
* `artifact.py:742` — `('artifact_ref', 'qeury', ...)`. A typo
  for `query`, and by W1 it is really `path`.

### W3 — Wrong parameter names (5, plus one pseudo-parameter)

| Endpoint | Declared | Handler accepts |
|---|---|---|
| `InstancesEndpoint.post` | `sshkey` | `ssh_key` |
| `InstancesEndpoint.post` | `userdata` | `user_data` |
| `NetworkEndpoint.get` | `artifact_ref` | `network_ref` |
| `NetworkEndpoint.delete` | `artifact_ref` | `network_ref` |
| `NodeEndpoint.get` | `node_name` | `node` |
| `NodeEndpoint.delete` | `node_name` | `node` |

`UploadDataEndpoint.post` declares a parameter literally named
`binary data`, which is not a kwarg and never can be — it
documents the raw request body. It needs a representation that
the compiler can recognise and skip rather than a rename; decide
between a reserved name and a distinct location token, and use
the same mechanism anywhere else a raw body is documented.

### W4 — Undeclared parameters (20)

Each is either a documentation gap to fill or a parameter to
deliberately hide. Decorator-injected objects (`*_from_db`,
`operation_from_db`) are neither — the compiler must know to
ignore them, which is a rule to write down rather than a
declaration to add.

Genuine gaps, all currently invisible in the published API:

| Endpoint(s) | Parameter |
|---|---|
| `InstanceOutstandingOperationsEndpoint.get`, and the artifact / network / agent-operation equivalents | `all` |
| `ArtifactMetadataEndpoint.delete`, and the auth / blob / interface / node equivalents | `value` |
| `LabelEndpoint.post` | `max_versions` |
| `AuthNamespaceTrustsEndpoint.post` | `external_namespace` |
| `InstancesEndpoint.post` | `ssh_key`, `user_data` (the real names from W3) |
| `NetworkEndpoint.get` / `.delete` | `network_ref` |
| `NetworkEndpoint.delete` | `namespace` |
| `NodeEndpoint.get` / `.delete` | `node` |

`value` on the metadata *delete* endpoints is worth a moment's
thought rather than a reflexive declaration: a delete that
accepts a value may be vestigial, in which case the fix is to
stop accepting it.

### W5 — Make `swagger_helper()` reject an unknown location

`swagger_helper()` already raises `KeyError` on an unknown type
token, via the `argtypes` dict lookup. It accepts any string as a
location, which is why `'post'` and `'qeury'` survived. Validate
the location against `{'query', 'body', 'path', 'header',
'formData'}` — the Swagger 2.0 set — so the next typo fails at
import time.

This is the change that stops W1–W4 from silently recurring for
locations. W6 does the same for everything else.

### W6 — A test that keeps the declarations honest

The measurements phase 0 ran by hand become a permanent test, so
that a future endpoint cannot reintroduce any of the above. It
asserts, over every endpoint class in `external_api/`:

1. every declared name is either a kwarg of the handler, a
   documented pseudo-parameter, or explicitly exempt;
2. every parameter appearing in the class's mounted routes is
   declared with location `path`;
3. no declared name is a decorator-injected object;
4. every handler kwarg is either declared or on an explicit
   ignore list, so a new undeclared parameter fails rather than
   quietly joining the 20.

Structural assertions on the parsed AST and the route table, not
substring matching on source text.

## Success criteria

- [x] All 119 path parameters declared as `path`, derived from
      the route table rather than by hand.
- [x] No location outside the Swagger 2.0 set, enforced at import
      time via `InvalidAPIDeclaration`.
- [x] The 5 wrong names corrected, and the raw-body
      pseudo-parameter given a representation the compiler can
      skip (`api_base.RAW_BODY_PARAMETER`).
- [x] Every handler kwarg either declared or explicitly exempt.
- [x] W6's test passes, and fails when a declaration is broken on
      purpose (verified against two deliberate breakages).
- [x] The generated specification was inspected — path parameters
      now render as `in=path`.
- [x] No production behaviour change: the full unit suite passes
      unmodified.

## Outcome

Six commits, one per worklist item plus the snapshot endpoint the
test found. What changed beyond the plan:

**W6 found an endpoint the audit could not.**
`InstanceSnapshotEndpoint` carries no `swag_from` at all, so both
its methods were absent from the published API — including
`max_versions`, which PR #3610 found flows unvalidated into
`Artifact.from_url()`. The by-hand measurements compared
declarations against handlers, so an endpoint with *no*
declaration was invisible to them. Declared as part of W6.

**One deferral.** The five metadata `delete` endpoints accept
`value` and none of them read it. The right fix is to stop
accepting it, not to document it, but removing it today makes a
caller who sends it receive `delete() got an unexpected keyword
argument 'value'` as a 400 — the exact leak this plan exists to
close. It is deferred to phase 4, when the schema layer rejects
unknown parameters cleanly, and is recorded in
`UNDECLARED_BY_DESIGN` in the test with that reasoning. The
official client has never sent it.

**The 11 `body` path parameters were all unintentional**, as
phase 0's D8 predicted: `interface_uuid`, `key_name` and
`namespace`, all ordinary path segments.

## Notes for the executing session

The two measurement scripts phase 0 used are worth rebuilding as
the basis of W6's test rather than as throwaway analysis — they
already do the AST walk and the `app.py` route extraction, and
the test is those two comparisons plus assertions.

Expect the diff to be large and boring: ~120 single-token edits
across 20 files. Keep it mechanical, generate it with a script,
and review the script rather than the diff. The interesting
commits are W5 and W6, which are what stop this recurring; W1–W4
are the one-time cleanup they protect.

One commit per worklist item, per the master plan's preference.
W5 should land before or with W1 so the corrected locations are
immediately enforced.
