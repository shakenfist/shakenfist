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

- [x] Every declared location is the one the code reads the value
      from — a name in a mounted route is `path`, a name in a
      webargs query schema or a `flask.request.args` read is
      `query`, everything else is `body` — derived rather than
      decided by hand, and enforced by
      `test_declared_locations_are_derivable`.

      Stated as a property because the counts moved during the
      phase and are only meaningful against the commit they were
      measured on. Against `b844fd98e`, before the audit added any
      declaration, 119 parameters appeared in a route and 3 of
      them were declared `path`; the branch now carries 128 `path`
      declarations, the growth coming from W3/W4's new
      declarations and from the Werkzeug-converter fix described
      below. The number will keep moving. The property will not.
- [x] No location outside the Swagger 2.0 set, and no type token
      outside the `argtypes` vocabulary, enforced at import time
      via `InvalidAPIDeclaration`.
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

### Corrected during review

Three defects in the first cut of this phase, all found by the
automated reviewer on PR #3620:

* **The route regex did not understand Werkzeug converters.**
  `<path:label_name>` did not match `<([a-z_]+)>`, so three
  `LabelEndpoint` declarations stayed wrong *and the test meant
  to catch that passed anyway*. The success criterion above was
  really 119 of 122. Both the fixer and the test now take the
  name after the last colon, which also covers `<int:x>` and
  `<uuid:x>`.
* **Three `all` declarations said `body` where the code says
  `query`.** The outstanding-operations endpoints carry
  `@use_kwargs(get_args, location='query')`, which updates kwargs
  *after* `log_request` merges the body, so a caller following
  the new documentation would have had their value silently
  overwritten by the default. Declaring drift while removing
  drift.
* **`version_id` was `path` with `required=False`,** which
  OpenAPI 2.0 forbids — a path parameter must be required — so
  the generated specification would have failed a linter.

The response was to make each derivable rather than to fix the
three instances. `tools/fix-api-parameter-locations.py` now
derives *every* location, not just `path`: a name in the route is
`path`, a name in a webargs query schema is `query`, everything
else is `body`, because `log_request` merges the JSON body and
nothing reads the query string. Re-running it is now the check
that the tree still agrees, and it reports no changes. That also
closed the query-versus-body drift the reviewer raised separately
— eight `key` and `target_*` parameters documented as query
strings which the client has always sent in the body.

`swagger_helper()` gained the path-implies-required rule
alongside the location check, and the test gained assertions for
webargs agreement, path requiredness, statically-unreadable
declarations, and a handler kwarg colliding with the raw-body
sentinel. A separate test case covers `swagger_helper()`'s
rejections directly, because the tree-scanning assertions read
declarations out of the AST and never execute one.

A fourth defect, found in the second review round: the
generalised derivation rule rewrote `ClusterOperationsEndpoint`'s
`target_object_type` and `target_uuid` to `body`, because they
appear in no route and no webargs schema. But that handler falls
back to `flask.request.args.get()` for both, so a raw
`?target_...=` GET keeps working, and that is the form AGENTS.md
documents. The phase whose purpose is making declarations true
made one endpoint less true. It also mattered downstream:
decision D6 grants a query-string fallback only to parameters
declared `query`, so compiling this would have deleted a fallback
the handler deliberately implements.

Fixed the same way as the others — by adding the missing
derivation source rather than the missing declaration. A name the
handler reads from `flask.request.args` is a query parameter
whatever else it also is, in both the fixer and the test. The
script now derives all four sources, and `header` and `formData`
declarations — derivable from none of them — are reported and
left alone instead of being rewritten to `body`.

The re-runnable check runs on every commit touching
`external_api/` as the `check-api-parameter-locations` pre-commit
hook.

A third round found that this was not the same thing as being
enforced. No workflow runs `pre-commit`, so the hook only fires
for contributors who have run `pre-commit install`, and the test
asserted two directions of the derivation rather than all of it:
path-to-path and query-source-to-query, but nothing said a
parameter declared `query` is read from the query string.
Declaring `event_type` on the blob events endpoint as `query`
passed every assertion while the script reported it — drift of
exactly the class W6 exists to prevent, and the class phase 3
compiles into a live query-string fallback under D6.

The derivation now lives in one place,
`shakenfist/external_api/declarations.py`, which both the script
and the test import; the test asserts that its `audit()` finds
nothing, which is the same question the script asks. That closes
the gap in CI and removes the duplication the reviewer raised
alongside it — five near-identical functions across the two
files, already diverged in how they resolved a non-literal
parameter name. `DerivationTestCase` covers the four sources on
constructed sources rather than on the tree, since the cases each
of them was added for are by definition not in the tree any more.

The webargs source became per-method and `use_kwargs`-aware in
the process: it now reads the decorator's `location` keyword and
resolves the schema argument it names, rather than treating any
class-level `get_args` dict as a query schema for every handler
in the class. That was a latent wrong answer for a class with a
webargs `get` beside a `post` declaring a same-named parameter.

A fourth round found that this fixed *handler* attribution but
not *name* resolution: the scopes searched for the schema name
were unioned, and the module was always one of them, so every
`get_args` in a file contributed to every handler in it. Two
endpoint classes in one module would have derived each other's
query parameters — and the fixer would then have rewritten a
correct `body` declaration to `query`, teaching phase 3 to accept
a parameter from the query string that never arrives there.
Resolution is now innermost-scope-first, and each scope
contributes only its own assignments. It could not fire yet:
each of the four modules with a webargs schema contains exactly
one.

The same round widened `test_every_endpoint_is_documented` from
"a handler with parameters must declare them" to "a handler must
carry a declaration", which is the property that would have
caught `InstanceSnapshotEndpoint` directly rather than by
accident — its `get` takes no parameters, so the narrower test
never covered it. Widening it turned up that "declares no
parameters" and "carries no declaration" are different questions:
eight endpoints correctly declare an empty list because they
accept nothing, and only the three unauthenticated health probes
(`Root`, `Livez`, `Readyz`) genuinely have no declaration. They
are now exempt by name and reason rather than by accident of
taking no arguments.

Three smaller things from the same round. `swagger_helper()`
checks the arity of a declaration tuple before destructuring it,
so a four- or six-element tuple raises `InvalidAPIDeclaration`
rather than the one `ValueError` that escaped the "one exception
type" goal. `handlers()` requires a `Resource` base rather than
trusting a method name, so a helper class with a `get()` accessor
is not asked to document itself. And `argtypes['integer']`
carried a duplicated `type` key where the second was meant to be
`format`, so integer parameters rendered without one while every
other token had it — a real if cosmetic change to the published
specification, made inside a change set otherwise described as a
location audit, and recorded here so a later bisect is not
confusing.

A fifth round found the same shape once more, in the assertion
next door: `test_accepted_parameters_are_declared` skipped any
handler whose declaration list was empty, which exempts a
`swag_from` whose parameters have been *emptied* while the
handler still accepts them. `documented()` existed by then —
added in round four for precisely this distinction — and was not
used here. Emptying `BlobEndpoint.get`'s list left all 27 tests
green while `blob_uuid` vanished from the published API.

The rest of that round closed the silent-skip class properly
rather than instance by instance. Every source in
`declarations.py` answered "not found" and "cannot read this"
with the same empty set, which makes a skipped input a confident
wrong answer rather than a missing one: an unreadable route
empties a class's path set, so every one of its parameters
derives to `body`, and the fixer rewrites correct `path`
declarations. `audit()` now returns a third value, `problems`,
which both consumers refuse to proceed past — the script exits
without rewriting, and the test asserts it *before* the drift
list, so the cause is reported rather than the symptom.

That ordering was found by the mutation pass described below,
not by review: with the problems assertion second, a route the
derivation could not read reported "`blob_uuid` is declared
`path` but arrives in the body" — a message that sends the reader
to change a correct declaration.

### Mutation-testing the guards

`tools/check-api-declaration-guards.sh` breaks each property the
audit claims and confirms the guard fires. Ten mutations: a path
parameter declared `query`, a body parameter declared `query`, an
emptied parameter list, a missing `swag_from`, an undeclared
kwarg, an optional path parameter, an unknown type token, a
four-element tuple, an unreadable route, and a decorator-injected
object declared as a parameter.

It exists because four of the five review rounds on the PR that
landed this phase found the same thing: an assertion that passed
for a reason other than the one it was written for. Reading a
guard cannot distinguish "this holds" from "this cannot fail";
breaking the tree can. Three of the ten surface as an aborted
test collection rather than a named failure, which is the
import-time enforcement working as designed.

Not wired into CI — it takes a couple of minutes and mutates the
tree — so it belongs in the pre-push checklist for changes to
this machinery.

### Known remaining gap: multiple body parameters

Swagger 2.0 permits at most one `in: body` parameter per
operation, and it must carry a `schema` rather than
`type`/`format`. `swagger_helper()` emits `type`/`format` for
every parameter regardless of location, and operations declaring
more than one body parameter go from **23 on develop to 29 on
this branch** — the metadata POST endpoints in particular move
`key` from query to body, joining `value`.

Each individual move is correct (the client does send `key` in
the body) and the defect predates this phase, but it means the
"anyone generating a client from the published specification gets
a broken client" problem this plan opens with is only partly
closed. Fixing it means restructuring how `swagger_helper()`
emits body parameters — collapsing the N declarations into one
`body` parameter with a generated `schema` object — which is
phase 2 work, not something to bolt onto a mechanical audit. It
is recorded as a work item there.

Nothing in CI would catch the next such regression: no test
validates the generated specification. Also a phase 2 item.

### Deferred to phase 2

Type-token normalisation. `namespace` is declared with the
`string` token throughout `auth.py` even though a `namespace`
token exists. Tokens are cosmetic until phase 2 makes them select
a validator, and normalising them before that vocabulary is
settled would be premature. The two `external_namespace`
declarations were made consistent with each other since one was
added here.

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
