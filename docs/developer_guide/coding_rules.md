# Coding rules learned the hard way

Each rule below came out of a real defect. They are not style
preferences: breaking one has previously shipped a bug. Read this before
touching authorisation predicates, parsers, lookup keys, or metrics.

## Never restate a visibility predicate

A listing endpoint filters; a single-object endpoint cannot, because it has
already resolved the object by the time it knows who is asking. The two must
still agree, so the single-object guard calls *the same function* the listing
filters with rather than open-coding the equivalent test.

For artifacts that function is `namespace_or_shared_filter(namespace, obj)`:
own namespace, a namespace whose trust list names the caller, `system`, or
`shared`. `requires_artifact_access` calls it.

`requires_artifact_ownership` is the deliberately stricter mutation guard and
tests `request_namespace() not in [obj.namespace, 'system']` — the same test
`requires_instance_ownership` and `requires_network_ownership` use. It
consults neither the `shared` flag nor the trust list: sharing publishes an
object for reading, and a trust is a visibility grant, so neither one hands
out a delete button. Creating an object *in* a namespace that trusts you is a
separate question and is still allowed.

This is not a style preference. `requires_artifact_access` used to restate the
rule as `if a.shared and requestor not in [a.namespace, 'system']`, which is
inverted in both directions, and because `arg_is_artifact_ref` resolves a UUID
straight to `Artifact.from_db` with no namespace filter, that decorator was the
only guard on the path. Any caller who knew a UUID could read any namespace's
unshared artifacts. Refuse with `404` rather than `403` so the refusal does not
confirm the object exists, and pair every new refusal test with a control that
shows the same request succeeding when the one thing under test changes.

The same predicate governs how a *name* resolves on the read routes, not just
whether a resolved object is allowed through. `arg_is_visible_artifact_ref`
(paired with `requires_artifact_access`) resolves through
`Artifact.from_db_by_ref_visible_to`, which searches the caller's own
namespace first and only then widens to what `namespace_or_shared_filter`
admits. Two rules to preserve if you touch this:

- The caller's own namespace must win, or sharing an object silently
  retargets every tenant who already used that name.
- Routes which change an object use plain `arg_is_artifact_ref` and resolve
  names narrowly. The ownership guard already refuses the write whichever way
  the object was named, so this is defence in depth rather than the only
  gate — but a name must never resolve into someone else's namespace on a
  route that then deletes what it found. New route, ownership guard, narrow
  ref decorator — the pairing goes together.

The same split applies to *url* resolution, and this is where it was missed.
`Artifact.from_url` filters by `namespace_or_shared_filter`, so it can return
an artifact belonging to whoever shares with or trusts the caller. That is the
right answer for a caller which will read the result and the wrong one for a
caller which will write to it: the upload and cache routes used it to pick a
write target, so a trusted namespace could name the owner's `source_url` and
have its own blob added as the newest version — and because `add_index` ends in
`delete_old_versions`, the owner's older versions went with it. Write paths use
`Artifact.owned_from_url()`, which resolves by ownership and does not create.

Two rules fell out of fixing it, and both generalise past artifacts:

- **A predicate is part of a function's contract, not an implementation
  detail.** If one lookup serves both intents, say which it is in the name and
  make the other one a separate function. A default that silently suits readers
  is how a write path inherits a read's authorisation.
- **Creating and modifying are different grants.** A trust may let a namespace
  *give* you an object it did not have — additive, and the operator guide
  promises it. It must not let that namespace replace what an object you
  already own resolves to. When a route can do either, authorise the two cases
  separately rather than once at the top, and put the audit event *after* the
  check so a refused caller cannot write to the event log of a namespace it is
  about to be told does not exist.

The remaining call sites were narrowed in #3640, and one of them was
worse than the sweep recorded. `LabelEndpoint.post` had been read as safe
because it builds its URL from the request — but `_label_url` accepts
`<namespace>/<label>` and hands back the namespace it was given, so any
authenticated caller could push a version into any namespace's label. The
`requires_admin=True` in its `swag_from` is documentation and enforces
nothing, and the route carried no ownership decorator, so nothing stopped it.
Two lessons worth carrying:

- **"Built from the request" is not the same as "not caller-controlled."**
  The URL was assembled by our code out of a value the caller chose. Follow
  the value, not the construction.
- **A read path can be broken in a way that hides the write path's bug.**
  `LabelEndpoint.get` and `delete` had answered 500 to every request since
  2024 (a pair handed to a filter expecting a string, and a 404 that was
  computed but never returned), so nobody exercised the endpoint hard enough
  to notice what `post` would accept.

The instance path is the case where the obvious narrowing was the wrong fix,
and it is worth knowing why before someone "simplifies" it:

- **Resolving `disk.base` by ownership alone would have broken sharing.**
  Reuse is the entire point of a shared image, and `transfer_image` treats an
  artifact with no versions as "cluster does not have a copy", so giving every
  namespace its own artifact would have meant its own download and its own
  stored copy of every shared image.
- The split is therefore per verb, not per artifact. A visible foreign
  artifact is resolved to a blob and booted from — the same move the label and
  snapshot branches already made — and never fetched into. `owned_from_url()`
  picks the write target; `from_url()` still picks what you may read.

`Artifact.owned_from_url_or_new()` exists for the write paths whose target
namespace is fixed as the caller's own, or already authorised: they have no
two cases to tell apart, so they get the create for free. Routes which accept
a caller-nominated namespace must still authorise creating and modifying by
hand, which is why `owned_from_url()` itself does not create. The artifact
fetch and upload routes both spell the two cases out for that reason, and the
apparent duplication between them is the authorisation rather than a missing
abstraction.

One more lesson, from the review of that change:

- **A sweep is only as wide as the directory it was run over.** The original
  #3640 audit listed three sites because it looked at `external_api/` and
  `operations/`. `Instance.snapshot()` is a fourth, in the core object, and
  neither the issue nor the first draft of the fix saw it. It was not
  exploitable — the URL carries the instance UUID and `type_filter` pins the
  type — but "not reachable today" is an argument for the guard being cheap,
  not for going without it. Grep for the *sink* (`add_index`), not for the
  callers of the resolver you happen to be changing.

## Credential-carrying routes are not logged, not redacted

Two independent loggers see a request body: `app.py`'s `before_request` audit
event, and `log_request` in `base.py`, which merges the parsed body into the
decorated method's kwargs and logs those. Both ask
`api_base.handles_credentials()` — one predicate, in one place — and drop the
whole body when it answers yes.

Redacting by field name was tried and is wrong. `key` means a metadata key
name on most endpoints and a secret on a few, so a name-based rule has to know
which route it is on anyway, and it silently starts leaking the day somebody
adds a route it has not heard of. That is exactly what happened: the federated
exchange's credential field is `token`, which was on neither redaction list,
so identity tokens were logged verbatim at INFO and shipped to the log
aggregator.

Every route which takes a credential lives under `/auth/`. Keep it that way,
or extend the predicate — never the redaction lists.

## A check that runs after the parse is not a check

The endpoint-method decorators are not the outermost thing in a request.
`log_request` calls `get_json(force=True)` before any method body runs, so a
size or shape check written inside a `post()` cannot prevent work that has
already happened. Anything protecting an *unauthenticated* endpoint from
attacker-controlled input has to be an `@app.before_request` hook registered
ahead of `log_request_info` — see `limit_federated_body_size`.

While you are there: `flask.request.content_length` is `None` for chunked
transfer encoding. Treating unknown as small enough lets any caller opt out of
a size limit by choosing a header, so refuse with 411 rather than measuring.

## Two records must not claim one lookup key

`federation.issuer_for_token` resolves a trusted issuer by scanning for a
matching `issuer_url`. Uniqueness on that column is therefore a correctness
property, not tidiness: two live records claiming one URL make which
provider's signing keys are trusted depend on listing order, so an
administrator repointing an issuer would believe they had while some requests
kept verifying against the old JWKS. The create and update endpoints refuse a
duplicate by calling `federation.issuer_claiming_url`, the same function the
resolution path uses.

A check-then-write is not an invariant on its own. `issuer_url` lives in the
attributes row and has no unique index -- and cannot easily get one, because a
soft-deleted issuer keeps its row and its URL is deliberately reusable -- so
both endpoints hold `_issuer_url_lock()` across the check *and* the write it
guards. Without that, two administrators configuring one provider at the same
moment both read "free" and both write. This is the `vsock_cids` pattern from
`instance.py`: where a unique index cannot be the arbiter, a cluster lock
around the check-then-act has to be.

## Put the meter above the expensive thing, not below it

`/auth/federated` is unauthenticated, so every step above the rate limit is a
step an anonymous caller gets for free, as often as they can send. Ordering
there is a security property, and the question to ask of each step is not "is
this cheap?" but "does this touch the database or the network?". Issuer
resolution reads once per configured issuer, so it belongs *below* the meter
even though there are only ever a handful of issuers; only the argument
checks, which touch nothing but the request, belong above it. The original
ordering had this backwards on its own stated logic -- it placed the meter
below the lookup to avoid writing a counter row, when the lookup it was
skipping cost more than the row did. If you add a step to that endpoint, place
it relative to `enforce_rate_limit` on that basis, and say why in a comment.

## A guard has to sit where the exception is raised

`CorruptMappingRule` comes from decoding `bound_claims` or `scopes`, which
only happens on the *attributes* read. `MappingRule.from_db_by_name` reads the
static row and the object state, so wrapping the lookup in a `try` looks like
protection and is none. Before writing a guard, find the raise site; before
believing a regression test, check that the thing it patches is on the path
that can actually fail. Use `MappingRule.policy()` to read the whole policy in
one go when you need more than one field -- it is also the single place to
catch this.

The same guard has to answer differently in different places, and that is not
inconsistency. The exchange endpoint *refuses* a damaged rule, because bound
claims it cannot read are bound claims it cannot check and minting anyway would
be authorising on a guess. `MappingRule.external_view()` *describes* one, with
an explicit `unusable` marker, because the CRUD routes exist to tell an owner
which rule is broken -- raising there turned one bad row into a 500 that hid
every healthy rule in the namespace, and made a successful delete report
failure on the one call that would have cleaned it up. Ask what the caller will
do with the answer before choosing.

## Fail closed on a field, not on a formatting accident

A reply that says "this went wrong" must say so in a field set only on the
success path. `CountFederatedAttemptReply` used to signal failure by carrying a
non-empty `error`, and the client read anything else as an answer -- so an
exception raised with no args, whose `str()` is empty, arrived as
`attempts=0, error=''` and was read as "nobody has tried this minute, allow".
That is the one direction a rate limiter must never fail, on the one endpoint
anybody may call.

Both federation replies now carry `bool ok`, set only where the work actually
succeeded, and the client tests that. `error` is diagnostics. When you add an
RPC whose reply has a permissive-looking default -- a zero count, an empty
list, a `False` that means "go ahead" -- carry an explicit success field rather
than inferring one, and write the test that returns the empty-error reply and
asserts the refusal. The invariant is not that today's code produces a message;
it is that no reply can be mistaken for a permissive one.

## `or []` is a decision about what a failed read means

`mariadb.get_objects_by_state()` returns `None` when the read failed and `[]`
when nothing matched, and says so in its docstring. Every `or []` at a call
site erases that distinction, and the erasure is not neutral: it asserts that
the caller treats "we could not find out" and "there is nothing" the same way.
Sometimes that is true. Decide it deliberately, because for one caller in this
codebase it was catastrophically false.

`get_active_blob_uuids()` ended in `or []`. The cleaner uses its result as a
*complement* set -- it unlinks every blob file on disk whose uuid is not in the
list -- so a failed read arrived as "no blobs are active", which is an
instruction to delete the node's entire blob store. The trigger was not exotic:
an oversized `GetObjectsByState` reply (issue 3638) is a non-retryable
`RESOURCE_EXHAUSTED`, raised as-is by `_grpc_call`, mapped to `None` by the
client wrapper, and flattened to `[]` one line later. The same call in the
cluster daemon only ever *iterates* the list, so there the empty list was
merely a skipped pass. One accessor, two callers, opposite consequences.

So the rule is about the caller, not the accessor. Before collapsing an error
into a value, ask what each caller does with it. A caller that iterates can
usually tolerate a skipped pass -- catch explicitly, log, and move on. A caller
that complements, gates, or diffs against the list cannot, and must see the
failure. `get_active_blob_uuids()` now raises `exceptions.DatabaseUnavailable`,
which is the same shape as the fix for issue 3373 (an unreachable database must
not be indistinguishable from a missing object) and gets the REST path a clean
503 for free via `handle_database_unavailable`.

A sweep that reads a work list has a quieter version of the same problem: it
does not delete anything wrongly, it just does nothing, reports a healthy pass
over an empty queue, and lets the backlog grow -- which in issue 3638 grew the
very reply that could not be read. `_sweep_work_list()` in the cluster daemon's
scheduled tasks is the shared answer: a failed read returns `None`, is counted
into `cluster_sweep_work_list_failure_streak`, and is logged as a skipped pass.
The general form: **silence is not success, and an empty result set is not the
absence of an answer.**

One trap when handling this deliberately: a failed read from the `mariadb`
client arrives in *two* shapes. The client wrappers map `grpc.RpcError` to a
`None`/`False`/`[]` return, but `_grpc_call()` raises
`exceptions.DatabaseUnavailable` once its retry budget is spent, and that is
deliberately not an `RpcError` subclass (issue 3373), so it propagates through
the wrapper untouched. Handling only the return value covers the oversized-reply
case and misses the tier outage -- which is the more likely reason a read fails,
and the condition an alert on the streak most needs to see. Cover both, and
prove it with a test that sets `side_effect = DatabaseUnavailable(...)` rather
than a `None` return.

## Cluster CI tests only run in the merge queue

The `(collection)` matrix in `Functional tests` -- everything under
`deploy/shakenfist_ci/cluster_ci_tests/` -- is skipped on `pull_request` and
runs on `merge_group`. A green PR therefore says nothing about whether those
tests pass, or whether they can even reach their first assertion.

`cluster_ci_tests/test_federation.py` sat through four commits registering a
trusted issuer with an `http://` `jwks_uri` while the API had refused
non-HTTPS `jwks_uri` since the object was added. Every test in the class died
in `setUp` on a 400, and nothing said so until the branch entered the queue.

Two habits follow. When you add or change a cluster CI test, run it, or at
minimum drive the validator it depends on directly -- these tests import
cleanly given `pip install shakenfist-client testtools oslo.concurrency
prettytable` in a scratch venv, so "the client is not installed" is not a
reason to skip verification. And when a test needs input that a validator
rejects, the validator is usually right: change the test, never carve a
loopback or test-only exemption into a security check. If that makes the test
unrunnable, make it skip loudly and file the issue -- see #3639 for the JWKS
certificate case.

That issue is a worked example of how the second habit tends to resolve. The
test needed the cluster to trust a certificate it had minted, and the tempting
fix was a test-only exemption in the deploy. What it became instead was
`FEDERATION_JWKS_CA_BUNDLE` -- extra trust anchors for JWKS fetches, which a
self hosted Authentik or Keycloak needs anyway -- with CI as its first user.
When a test cannot run because a security check is doing its job, the useful
question is usually "what would a real operator need here", not "how do we get
around this in CI".

Two traps it also left behind, both of which cost a debugging round:

- **`ssl.create_default_context(cafile=...)` replaces the system trust store
  rather than adding to it.** Build the default context and then call
  `load_verify_locations` on it. The wrong spelling passes every test that
  only checks the new anchor is present.
- **Python 3.13 enables `ssl.VERIFY_X509_STRICT` by default**, so a leaf
  certificate with no Authority Key Identifier is refused with "Missing
  Authority Key Identifier". If you generate certificates in a test, give
  them AKI, SKI and basic constraints -- and be aware the symptom is
  indistinguishable from the CA not being trusted at all.

### If a cluster test can run on one node, share it with the smoke suite

`cluster_ci_tests/test_database_tier.py` landed with two independent defects at
once (#3694, #3708) and blocked the merge queue for four days, because the
merge queue was the first place it ever ran. Both defects were reproducible on
a single node.

The two suites are disjoint directories -- `smoke-ci.conf` discovers only
`smoke_ci_tests/`, `cluster-ci.conf` only `cluster_ci_tests/` -- so "add it to
smoke" cannot mean moving it without losing the multi-node coverage. Define the
test bodies once in a mixin under `deploy/shakenfist_ci/` (not in either suite
directory, or stestr collects it twice from one run) and subclass the mixin
from both suites. `database_tier.DatabaseTierTestsMixin` is the worked example:
the two tests needing one sf-database run in both, and the load-balancer test
needing N>=2 stays in the cluster suite where its skip is honest.

Prefer this whenever a cluster test's preconditions are met by the single-node
`localhost` topology. Sometimes that topology is also the *better* test: the
API and sf-database share a machine there, so it is the most exposed to the
direct-MariaDB routing regression #3708 was about.

### tearDown runs before addCleanup, and the base class already deletes

`BaseNamespacedTestCase.tearDown()` deletes every instance in the namespace and
blocks until they are gone, swallowing `ResourceNotFoundException` as it goes.
testtools runs `tearDown()` *before* the `addCleanup` stack:

```
ORDER: ['tearDown', 'cleanup_from_test', 'cleanup_from_setUp']
```

So `self.addCleanup(self.test_client.delete_instance, uuid)` in a namespaced
test can only ever 404, and unlike tearDown's own deletes it is not guarded --
it fails the test after the assertions have passed. This is what kept
`test_instance_get_fetches_the_attributes_row_once` red even once its
measurement bug was fixed. Do not register instance cleanups in a namespaced
test; the base class already reaps them. Reserve `addCleanup` for state the
base class knows nothing about, such as the host devices in
`test_stray_vxlan.py` or a namespace the test made itself.
