# Phase 6: Required and scalar semantics

Phase 6 of [`PLAN-api-input-validation.md`](PLAN-api-input-validation.md),
following [phase 5](PLAN-api-input-validation-phase-05-narrow.md).

Phase 4 turned rejection on for everything except required-ness. Phase 5
deleted the broad `except TypeError` that had been absorbing the
consequences. This phase answers the question phase 3 deferred as
decision D17 and phase 4 left in place — whether a parameter declared
`required` is actually required — and gives the semantic type tokens
phase 2 added something to do beyond documenting themselves.

**Planning effort:** high. The code changes are small; deciding which
of 75 declarations is telling the truth is not, and the previous phase
demonstrated how to get a survey of exactly this shape wrong.

**Review effort:** high. A reviewer's job here is to find a parameter
this phase marks required which some working client omits today.

## Amendments

**2026-09-13.** Two of the issues this plan was written against moved
between the planning commit and the phase starting. Both are corrected
throughout the text below; this note records what changed and why, so a
reader who remembers the original does not think the plan has drifted.

* **#534 was fixed outside this plan.**
  [PR #4183](https://github.com/shakenfist/shakenfist/pull/4183) landed
  as `2fcc0467f` on 2026-09-12, about four hours after the planning PR
  merged, and closed the issue. The automated issue fixer took it. Its
  fix is the one step 5 briefed and then some: `MACADDR_PATTERN` and
  `valid_macaddr()` in `shakenfist/util/network.py:417`,
  `ARGTYPES['macaddr']['pattern']` repointed at the constant
  (`base.py:408`), the guard placed in `_netdesc_safety_checks`
  (`instance.py:347`) so **both** callers are covered — instance create
  at `instance.py:829` and interface hotplug at `instance.py:1153`, a
  drift test pinning the published pattern to the validator, and a guest
  CI case in `guest_ci_tests/test_networking.py:122`. Step 5 keeps only
  #323; definition-of-done item 9 is gone because it is already true.
* **#936 was closed as a duplicate.** Closed 2026-09-12 as a duplicate
  of [#528](https://github.com/shakenfist/shakenfist/issues/528) at a
  narrower scope, in a cleanup sweep of the oldest open issues. The
  *work* is unchanged — element schemas for `dict` and `arrayofdict`
  are still unbuilt and still needed — so the phase 7 split stands on
  the same reasoning. Only the tracker moves: every reference below now
  cites #528, since a plan that sends an implementing agent to a closed
  issue wastes their first half hour.

This is the second phase running to lose scope this way. The master
plan already records phase 5 losing three of its four attribution
issues to the same fixer, and phase 6 named #534 in a *merged* plan
and still lost the race. The mitigation, for whoever files next: apply the
`automated-fix-attempted` label when filing, which reserves an issue
against the fixer while a branch is in flight.

## Context

`CompiledEndpoint` records required-ness and never enforces it
(`shakenfist/external_api/validation.py:82`), and `validate_request`
filters it out of the enforceable set even on the default `enforce`
path (`shakenfist/external_api/base.py:1906`):

```python
enforceable = [f for f in findings
               if f.reason != validation.MISSING_REQUIRED]
```

The docstring on `CompiledEndpoint` explains why, and names the
example: `mode` on the agent-put endpoint is declared required while
omitting it has always been accepted. The finding is still generated
and still logged, so the measurement apparatus has been running on the
default path since phase 4 — it is telemetry for this phase's decision,
which is what the comment says it is for.

Meanwhile every compiled field is `allow_none=True`, so an omitted
parameter and an explicit JSON `null` both reach the handler as `None`.
[#4167](https://github.com/shakenfist/shakenfist/issues/4167), filed by
phase 5, is the consequence: a handler that dereferences such a
parameter without a type guard turns a caller's mistake into a recorded
500. That issue says in its own words that it is phase 6's work, and
this plan agrees — **enforcing `required` is the systematic fix for the
top-level half of #4167**, and the two should not be solved twice.

## Scope

**In:**

* Decide, per declaration, whether `required=True` is true, and correct
  the ones that are not.
* Enforce required-ness once the declarations are honest.
* Attach real validators to the semantic type tokens, so `base64`,
  `netblock`, `ipv4`, `url` and `uuid` constrain what they claim to
  (13 body/query declarations, enumerated below).
* [#3269](https://github.com/shakenfist/shakenfist/issues/3269) — reject
  non-base64 `user_data` at the API instead of on the hypervisor.
* [#323](https://github.com/shakenfist/shakenfist/issues/323) — reject a
  network netblock which overlaps the floating network.

**Out:**

* [#534](https://github.com/shakenfist/shakenfist/issues/534) — the
  caller-supplied MAC address format. In scope when this plan was
  written, fixed by #4183 before the phase started; see *Amendments*.
* **Element schemas for `dict` and `arrayofdict`.** This is the other
  half of #4167 and the whole of
  [#528](https://github.com/shakenfist/shakenfist/issues/528), and it is
  a vocabulary change the size of phase 2. It gets its own phase — see
  *A phase split* below.
* Response validation (decision D7, out of scope for the plan, not
  deferred).
* The error shape, and in particular answering with more than one
  finding at a time. `validate_request`'s docstring offers phase 6 that
  argument "if callers ask for it". No caller has.
* [#2094](https://github.com/shakenfist/shakenfist/issues/2094) — the
  already-unrouted `DELETE` returning 403. Phase 5 ruled it out of scope
  as an idempotency question rather than a validation one, and nothing
  here changes that.

## What the survey found

The master plan's one-line description of this phase is accurate as far
as it goes. Everything below is detail it could not have had, plus one
claim that is now wrong.

### F1. Every declared-required body parameter is optional in fact

There are 349 parameter declarations. 147 are `path`, where required-ness
is a tautology — the route does not match without the segment. Of the
remaining 76 declared `required=True`, **75 are body or query parameters
whose handler gives them a default**, and one (`namespace` on
`AuthFederatedEndpoint.post`) is consumed by a decorator rather than
named by the handler.

Not one is structurally required. Python would not have raised for a
single omission.

The census is reproducible; the script is the appendix to this plan. Its
shape matters as much as its answer: it asks whether the handler
*accepts* an omission, which is the question that decides whether
enforcement is a contract change. Finding F5 of phase 5 asked the
adjacent question — whether any parameter *lacked* a default — got zero,
and drew a conclusion the zero did not support. The two questions have
the same code and opposite meanings.

So "enforce required" is a contract change for 75 declarations, and the
only way to know which of them is safe is to find out what each handler
does with the omission. That is step 1, and it is the bulk of this
phase.

### F2. The production numbers exist, and they are thin

26 days of sfcbr (`{job="shakenfist"}`, 2026-08-13 to 2026-09-08) carry
358 validation findings:

| Reason | Count | Notes |
|--------|-------|-------|
| `type-mismatch` | 336 | 294 of them the metadata `value` class phase 4 fixed by widening fourteen declarations to `any`; 40 on `POST .../claims`; 2 on the events `limit` |
| `missing-required` | 20 | `POST /auth` (12) and `POST .../claims` (8) |
| `unknown-parameter` | 2 | |

**Every one of the 20 `missing-required` findings was on a request that
already answered 400.** Enforcing required would not have changed a
single observed outcome. Both routes are credential-handling, so
`_handles_credentials()` redacted the parameter names — the reason code
survives, the name does not.

Two cautions on this table. Every record carries `validation-mode:
warn`, and the newest is 2026-09-08, the day before phase 4 merged; no
post-enforce record exists, and sf-api start-up lines are not shipped to
this tenant, so the survey **could not establish what mode sfcbr runs
today**. And sfcbr's traffic is overwhelmingly CI, which exercises a
narrow slice of the API — 2 routes out of the 75 declarations at issue.

That is why this phase does not open a warn window; see D35.

### F3. Exactly one type token validates anything

Phase 2 added `unsignedinteger`, `macaddr`, `base64` and `netblock` and
an optional constraints element. `_field()`
(`shakenfist/external_api/validation.py:112`) compiles `type`,
`minimum`/`maximum`, `pattern` and `items`. **It does not read `format`
at all**, except to recognise the `any` sentinel.

Every semantic token therefore renders a `format` string into the
published OpenAPI and compiles to a bare `fields.String`:

| Token | Compiles to | Validates |
|-------|-------------|-----------|
| `macaddr` | String + pattern | the format — the only one that does |
| `base64`, `netblock`, `ipv4`, `url`, `uuid`, `uuidorname`, `namespace`, `node` | String | that it is a string |
| `unsignedinteger` | Integer + Range(min=0) | the bound |

And `macaddr`, the one token that works, **is declared nowhere**. Its
only occurrence outside `ARGTYPES` is a comment in `base.py:518` citing
it as the example of an anchored pattern.

The 13 body/query declarations using a semantic token, which are this
phase's whole surface for D33:

| Endpoint | Method | Parameter | Token | Line |
|----------|--------|-----------|-------|------|
| `ArtifactsEndpoint` | post | `url` | url | `artifact.py:434` |
| `ArtifactUploadEndpoint` | post | `upload_uuid` | uuid | `artifact.py:547` |
| `ArtifactUploadEndpoint` | post | `blob_uuid` | uuid | `artifact.py:550` |
| `ArtifactUploadEndpoint` | post | `source_url` | url | `artifact.py:555` |
| `AuthIssuersEndpoint` | post | `jwks_uri` | url | `auth.py:896` |
| `AuthIssuerEndpoint` | put | `jwks_uri` | url | `auth.py:954` |
| `ClusterOperationsEndpoint` | get | `target_uuid` | uuid | `clusteroperation.py:305` |
| `InstancesEndpoint` | post | `user_data` | base64 | `instance.py:512` |
| `InstancesEndpoint` | post | `nvram_template` | url | `instance.py:535` |
| `InstanceAgentPutEndpoint` | post | `blob_uuid` | uuid | `instance.py:1895` |
| `LabelEndpoint` | post | `blob_uuid` | uuid | `label.py:107` |
| `NetworksEndpoint` | post | `netblock` | netblock | `network.py:229` |
| `NetworkDNSAddressEndpoint` | post | `value` | ipv4 | `network.py:743` |

`namespace` (63 body uses) and `node` (3) are deliberately excluded: a
ref decorator resolves them against the database and answers 404, which
is a stronger check than a format one and already runs.

### F4. #3269 is exactly as filed, four years on

`user_data` is declared `base64` at `instance.py:512`, which as F3
establishes enforces nothing. The only `b64decode` of it is
`shakenfist/instance.py:1930`, inside config-drive creation, unguarded:

```python
if self.user_data:
    user_data = base64.b64decode(self.user_data)
```

That runs on the hypervisor, after the API has answered 200 and the
instance has been scheduled and placed. A `binascii.Error` there is the
traceback the issue describes.

### F5. #323 is not a format problem, and #534 was not a top-level one

Both needed saying, because "semantic validators for #534, #3269, #323"
in the master plan reads as though all three are the same kind of work.
They are not:

* **#323** — `NetworksEndpoint.post` calls `ipaddress.ip_network()` and
  refuses anything below /29 (`network.py:56`). There is no check
  against the floating network. It cannot be a schema check: whether a
  netblock overlaps depends on the deployed floating network, which is
  cluster configuration the compiled schema has no access to. It is a
  handler guard.
* **#534** — the caller-supplied MAC arrives inside a *networkspec*, as
  `netdesc['macaddress']`, and is consumed at
  `shakenfist/network/interface.py:143`. At planning time
  `_netdesc_safety_checks` validated `network_uuid` and the requested
  address range and never looked at `macaddress`. Because networkspecs
  are declared `dict`/`arrayofdict`, **no schema can reach it** — see
  F6 — so it had to be a handler guard too. It now is one, added by
  #4183 rather than by this phase (see *Amendments*), which leaves the
  finding standing: the guard is at `instance.py:347` precisely because
  the schema layer cannot see that far down.

### F6. Nested structures are unvalidated by construction

`dict` compiles to `fields.Dict()` and `arrayofdict` to
`fields.List(fields.Dict())`, neither carrying a value schema. Every
key inside a diskspec, networkspec or videospec is invisible to the
validation layer in every mode. This is the structural cause behind
#528, #534, and items 2 and 3 of #4167 — and #534 is the proof, since
the only way to reject a malformed MAC today is a hand-written guard in
the handler.

It is real, it is worth fixing, and it is not this phase. See below.

### F7. A phase split, and a correction to the master plan

The master plan's phase 6 row bundles required-ness with "semantic
validators for #534, #3269, #323, #936". The survey says the last does
not belong with the others: the first three are guards and scalar
validators, while #936 — now closed as a duplicate of
[#528](https://github.com/shakenfist/shakenfist/issues/528), which is
the tracker the rest of this plan cites — is a vocabulary change:
element schemas for `dict` and `arrayofdict`, plus a decision about
how much of `InstancesEndpoint.post` can become declarative at all,
given that roughly 130 of its lines interleave validation with blob,
label and artifact *resolution* that no schema can express.

**This plan therefore splits phase 6 and renumbers the push audit.**
The master plan's Execution table and the `docs/plans/index.md` row are
corrected as part of the planning commit:

| Phase | Was | Now |
|-------|-----|-----|
| 6 | Required and semantics | Required and scalar semantics (this plan) |
| 7 | Push audit | **Structured parameter schemas** — #528, the declarative form of the #534 guard, #4167 items 2 and 3 |
| 8 | — | Push audit |

The index arithmetic moves from `6 of 8` to `6 of 9`. The push audit
stays last by construction: it audits the accumulated diff of every
phase.

### Nothing else in the phase description was wrong

The `except TypeError` narrowing the row once described belongs to
phase 5 and is done. The three attribution issues it also listed are
closed.

## Sweep results

Step 1's measurement, and the evidentiary basis for steps 2 and 3.

Produced by `shakenfist/tests/external_api/test_required_sweep.py` at
`a5f12bec5`, which enumerates the declarations from
`declarations.handlers()` -- the census script in the appendix, not a
hand written list -- and drives one real authenticated request per row
through the whole decorator stack. Step 1 ran it in the default
`enforce` mode, which was then still exempting missing-required
findings; step 3 deleted that exemption and moved the file to `warn`,
which reproduces the same pre-enforcement answers and is what the table
is re-measured against on every run (see *Progress*). For
each declaration it sends a complete valid request (the *control*) and
then the same request with that one parameter removed, recording the
status and whether `record_exception` was called.

The controls are not incidental. A request which 404s because a fixture
was not built is indistinguishable, from the outside, from a handler
refusing an omission, and that is precisely the shape of mistake the
phase 5 survey made. Every recipe therefore declares the status its
complete request answers and the test asserts it on every run, so a
rotted fixture fails the test rather than quietly publishing a wrong
verdict. Four verdicts in a first draft turned out to be fixture
artefacts and were caught this way, including one -- `blob_uuid` on the
agent-put route -- where a blob stub that resolved *any* uuid made a
fault look like an acceptance.

The table is pinned in the test as `SWEEP`, so a handler which starts
answering an omission differently fails CI rather than silently
invalidating this evidence.

**76 rows, the number the census reports.** 75 of them are finding F1's
declarations whose handler gives them a default; the 76th is the
raw-body marker discussed below.

| Verdict | Count |
|---------|-------|
| `guarded` -- the handler answers 4xx | 65 |
| `faults` -- 5xx, or an exception was recorded | 5 |
| `accepted` -- 2xx | 6 |

Step 1 measured 63 `guarded` and 7 `faults`. The review round moved two
rows by fixing the two faults which were bugs rather than symptoms of
required-ness; see *What the sweep says that F1 did not* below.

| Endpoint | Method | Parameter | In | Type | Status | Verdict | What the server does |
|----------|--------|-----------|----|------|--------|---------|----------------------|
| `ArtifactMetadataEndpoint` | put | `value` | body | any | 400 | **guarded** | `no value specified` |
| `ArtifactMetadatasEndpoint` | post | `key` | body | string | 400 | **guarded** | `no key specified` |
| `ArtifactMetadatasEndpoint` | post | `value` | body | any | 400 | **guarded** | `no value specified` |
| `ArtifactsEndpoint` | delete | `confirm` | body | boolean | 400 | **guarded** | `parameter confirm is not set true` |
| `ArtifactsEndpoint` | post | `shared` | body | boolean | 200 | **accepted** | defaults to False; the artifact is created unshared |
| `ArtifactsEndpoint` | post | `url` | body | url | 500 | **faults** | `Artifact.new()` does `source_url.split('/')` (`artifact.py:355`) |
| `AuthEndpoint` | post | `key` | body | string | 400 | **guarded** | `missing key in request` |
| `AuthEndpoint` | post | `namespace` | body | string | 400 | **guarded** | `missing namespace in request` |
| `AuthFederatedEndpoint` | post | `namespace` | body | string | 400 | **guarded** | `no namespace specified` |
| `AuthFederatedEndpoint` | post | `rule` | body | string | 400 | **guarded** | `no rule specified` |
| `AuthFederatedEndpoint` | post | `token` | body | string | 400 | **guarded** | `no token specified` |
| `AuthIssuerEndpoint` | put | `audience` | body | string | 400 | **guarded** | `no audience specified` |
| `AuthIssuerEndpoint` | put | `issuer_url` | body | string | 400 | **guarded** | `no issuer_url specified` |
| `AuthIssuerEndpoint` | put | `jwks_uri` | body | url | 400 | **guarded** | `no jwks_uri specified` |
| `AuthIssuersEndpoint` | post | `audience` | body | string | 400 | **guarded** | `no audience specified` |
| `AuthIssuersEndpoint` | post | `issuer_url` | body | string | 400 | **guarded** | `no issuer_url specified` |
| `AuthIssuersEndpoint` | post | `jwks_uri` | body | url | 400 | **guarded** | `no jwks_uri specified` |
| `AuthIssuersEndpoint` | post | `name` | body | string | 400 | **guarded** | `no name specified` |
| `AuthMetadataEndpoint` | put | `value` | body | any | 400 | **guarded** | `no value specified` |
| `AuthMetadatasEndpoint` | post | `key` | body | string | 400 | **guarded** | `no key specified` |
| `AuthMetadatasEndpoint` | post | `value` | body | any | 400 | **guarded** | `no value specified` |
| `AuthNamespaceClaimsEndpoint` | post | `expires_in_seconds` | body | integer | 400 | **guarded** | `no expires_in_seconds specified` |
| `AuthNamespaceClaimsEndpoint` | post | `limit_cpus` | body | unsignedinteger | 400 | **guarded** | `no limit_cpus specified` |
| `AuthNamespaceClaimsEndpoint` | post | `limit_disk_gb` | body | unsignedinteger | 400 | **guarded** | `no limit_disk_gb specified` |
| `AuthNamespaceClaimsEndpoint` | post | `limit_memory_mb` | body | unsignedinteger | 400 | **guarded** | `no limit_memory_mb specified` |
| `AuthNamespaceKeyEndpoint` | put | `key` | body | string | 400 | **guarded** | `no key specified` |
| `AuthNamespaceKeysEndpoint` | post | `key_name` | body | string | 400 | **guarded** | `no key name specified` |
| `AuthNamespaceRuleEndpoint` | put | `bound_claims` | body | dict | 400 | **guarded** | `missing required field(s): bound_claims` |
| `AuthNamespaceRuleEndpoint` | put | `issuer` | body | string | 400 | **guarded** | `missing required field(s): issuer` |
| `AuthNamespaceRuleEndpoint` | put | `key_name_prefix` | body | string | 400 | **guarded** | `missing required field(s): key_name_prefix` |
| `AuthNamespaceRuleEndpoint` | put | `key_ttl` | body | integer | 400 | **guarded** | `missing required field(s): key_ttl` |
| `AuthNamespaceRuleEndpoint` | put | `scopes` | body | arrayofstring | 400 | **guarded** | `missing required field(s): scopes` |
| `AuthNamespaceRulesEndpoint` | post | `bound_claims` | body | dict | 400 | **guarded** | `missing required field(s): bound_claims` |
| `AuthNamespaceRulesEndpoint` | post | `issuer` | body | string | 400 | **guarded** | `missing required field(s): issuer` |
| `AuthNamespaceRulesEndpoint` | post | `key_name_prefix` | body | string | 400 | **guarded** | `missing required field(s): key_name_prefix` |
| `AuthNamespaceRulesEndpoint` | post | `key_ttl` | body | integer | 400 | **guarded** | `missing required field(s): key_ttl` |
| `AuthNamespaceRulesEndpoint` | post | `name` | body | string | 400 | **guarded** | `no name specified` |
| `AuthNamespaceRulesEndpoint` | post | `scopes` | body | arrayofstring | 400 | **guarded** | `missing required field(s): scopes` |
| `AuthNamespaceTrustsEndpoint` | post | `external_namespace` | body | namespace | 400 | **guarded** | `no external namespace specified` |
| `AuthNamespacesEndpoint` | post | `namespace` | body | string | 400 | **guarded** | `no namespace specified` |
| `BlobMetadataEndpoint` | put | `value` | body | any | 400 | **guarded** | `no value specified` |
| `BlobMetadatasEndpoint` | post | `key` | body | string | 400 | **guarded** | `no key specified` |
| `BlobMetadatasEndpoint` | post | `value` | body | any | 400 | **guarded** | `no value specified` |
| `ClusterOperationsEndpoint` | get | `target_object_type` | query | string | 400 | **guarded** | `target_object_type parameter is required` |
| `ClusterOperationsEndpoint` | get | `target_uuid` | query | uuid | 400 | **guarded** | `target_uuid parameter is required` |
| `InstanceAgentExecuteEndpoint` | post | `command_line` | body | string | 200 | **accepted** | queues an `execute` operation whose `commandline` is null |
| `InstanceAgentGetEndpoint` | post | `path` | body | string | 200 | **accepted** | queues a `get-file` operation whose `path` is null |
| `InstanceAgentPutEndpoint` | post | `blob_uuid` | body | uuid | 404 | **guarded** | `blob not found` (was a 500 until the review round fixed `self.api_error`) |
| `InstanceAgentPutEndpoint` | post | `mode` | body | string | 406 | **guarded** | `invalid mode: a mode must be a string` (was a 500 until the review round refused a non-string mode) |
| `InstanceAgentPutEndpoint` | post | `path` | body | string | 200 | **accepted** | queues `put-blob` and `chmod` operations whose `path` is null |
| `InstanceInterfacesEndpoint` | post | `network` | body | dict | 400 | **guarded** | `network specification should contain JSON objects` |
| `InstanceMetadataEndpoint` | put | `value` | body | any | 400 | **guarded** | `no value specified` |
| `InstanceMetadatasEndpoint` | post | `key` | body | string | 400 | **guarded** | `no key specified` |
| `InstanceMetadatasEndpoint` | post | `value` | body | any | 400 | **guarded** | `no value specified` |
| `InstancesEndpoint` | delete | `confirm` | body | boolean | 400 | **guarded** | `parameter confirm is not set true` |
| `InstancesEndpoint` | post | `cpus` | body | unsignedinteger | 500 | **faults** | `InstanceData` refuses a null `cpus` (`instance.py:360`) |
| `InstancesEndpoint` | post | `disk` | body | arrayofdict | 400 | **guarded** | `instance must specify at least one disk` |
| `InstancesEndpoint` | post | `memory` | body | unsignedinteger | 500 | **faults** | `InstanceData` refuses a null `memory` (`instance.py:360`) |
| `InstancesEndpoint` | post | `name` | body | string | 400 | **guarded** | `instance name must be specified` |
| `InterfaceMetadataEndpoint` | put | `value` | body | any | 400 | **guarded** | `no value specified` |
| `InterfaceMetadatasEndpoint` | post | `key` | body | string | 400 | **guarded** | `no key specified` |
| `InterfaceMetadatasEndpoint` | post | `value` | body | any | 400 | **guarded** | `no value specified` |
| `LabelEndpoint` | post | `blob_uuid` | body | uuid | 500 | **faults** | `add_index(None)` raises `BlobMissing` (`artifact.py:665`) |
| `NetworkDNSAddressEndpoint` | delete | `name` | body | string | 406 | **guarded** | `invalid DNS name` |
| `NetworkDNSAddressEndpoint` | post | `name` | body | string | 406 | **guarded** | `invalid DNS name` |
| `NetworkDNSAddressEndpoint` | post | `value` | body | ipv4 | 200 | **accepted** | stores `hosteddns[name] = None` and enqueues the dnsmasq update |
| `NetworkMetadataEndpoint` | put | `value` | body | any | 400 | **guarded** | `no value specified` |
| `NetworkMetadatasEndpoint` | post | `key` | body | string | 400 | **guarded** | `no key specified` |
| `NetworkMetadatasEndpoint` | post | `value` | body | any | 400 | **guarded** | `no value specified` |
| `NetworksEndpoint` | delete | `confirm` | body | boolean | 400 | **guarded** | `parameter confirm is not set true` |
| `NetworksEndpoint` | post | `name` | body | string | 500 | **faults** | `NetworkData` refuses a null `name` (`network.py:277`) |
| `NetworksEndpoint` | post | `netblock` | body | netblock | 400 | **guarded** | `cannot parse netblock: None does not appear to be an IPv4 or IPv6 network` |
| `NodeMetadataEndpoint` | put | `value` | body | any | 400 | **guarded** | `no value specified` |
| `NodeMetadatasEndpoint` | post | `key` | body | string | 400 | **guarded** | `no key specified` |
| `NodeMetadatasEndpoint` | post | `value` | body | any | 400 | **guarded** | `no value specified` |
| `UploadDataEndpoint` | post | `body` | body | binary | 200 | **accepted** | appends nothing and answers the unchanged length; this is the raw body marker, which never reaches `required_names` |

### What the sweep says that F1 did not

**Step 1 measured 7 `faults`, and two of them were bugs independent of
required-ness. The review round fixed both, which is why the table above
now reads 5.** Each was a fault on far more than the omission path, and
each contradicted the response list the endpoint publishes -- which is
the class of defect this phase exists to close.

* `InstanceAgentPutEndpoint.post` wrote its blob refusal as
  `self.api_error(404, 'blob not found')` (`instance.py:1955`).
  `api_base.Resource` has no `api_error`, so *any* caller naming a blob
  which did not resolve -- not merely one who omitted the parameter --
  got an `AttributeError`, a recorded exception and an opaque 500
  instead of the documented 404. Enforcing required-ness would have
  closed the omission path and left the far more common bad-uuid path
  exactly as it was, so [#4194](https://github.com/shakenfist/shakenfist/issues/4194)
  is fixed here instead: the call is now `sf_api.error(404, 'blob not
  found')`, like every other refusal in the same handler, and the row
  measures `404`/`guarded`.
* The `mode` guard immediately above it was `try: int(mode) except
  ValueError`, and `int(None)` raises `TypeError`. This is the example
  `CompiledEndpoint`'s docstring cites as a parameter "declared
  required while omitting it has always been accepted"; that was true
  while `handle_authorization_exceptions` still caught `TypeError` and
  answered 400, and phase 5 deleted that arm (decision D23). It became
  a recorded 500, reachable under `warn` and `off` whether or not the
  parameter was omitted. [#4195](https://github.com/shakenfist/shakenfist/issues/4195)
  is fixed here by refusing a non-string mode with the `406` the
  endpoint publishes. Widening the `except` to `(TypeError,
  ValueError)` is *not* sufficient and was measured not to be: the
  symbolic fallback beneath it calls `.split()` on its argument, so a
  null falls out of it as an `AttributeError` and the 500 survives. An
  explicit type check is what closes the row.

The remaining five faults are a null reaching a constructor:
`Artifact.new()` splits `source_url`, `Instance.new()` and
`Network.new()` hand a null to a pydantic model which refuses it, and
`Artifact.add_index(None)` raises `BlobMissing`. All five are closed by
enforcing required-ness, which is D32's argument restated in
measurements -- and unlike the two above, none of them is reachable by a
caller who supplies the parameter, so there is nothing to fix in the
handler.

**The 6 `accepted` are the contract change.** Four of them are the three
agent routes, which cheerfully queue an operation carrying a null
`commandline` or `path` for the guest agent to fail on later; one is
`NetworkDNSAddressEndpoint.post`, which stores a DNS name pointing at
null; and one -- `shared` on `ArtifactsEndpoint.post` -- is a genuine
optional parameter with a sensible default which has simply been
declared wrongly. Only that last one is a declaration step 2 should
relax on the evidence here; the other five are cases where the
declaration is right and the handler is what is lenient.

**The raw body is a special case, and F1 mis-identifies it.** The census
counts one declaration which is not a handler keyword argument. F1 names
that as `namespace` on `AuthFederatedEndpoint.post`, "consumed by a
decorator rather than named by the handler". That is wrong: the
signature is `post(self, token=None, namespace=None, rule=None)`, so the
parameter has a default like the other 74. The actual odd one out is
`UploadDataEndpoint.post`'s `body`, declared with
`api_base.RAW_BODY_PARAMETER`. `compile_parameters()`
(`validation.py:225`) sets `raw_body` for it and `continue`s *before*
adding anything to `required_names`, so this declaration can never
produce a missing-required finding and step 3's enforcement will never
reach it whatever step 2 does to the tuple. The counts F1 reports --
76, 75, 0, 1 -- are unchanged and were re-run on this branch; only the
example is corrected.

**The better-message risk is smaller than feared.** The plan's risks
section worries that enforcement replaces a handler's specific message
with a generic one. 65 of 76 rows are already a 4xx whose message names
the problem, so step 3 will replace 65 hand-written messages with
`<parameter>: declared required but not supplied`. (63 when step 1
measured; the two the review round fixed joined them.) Most are already
generic (`no value specified` on 14 rows, `no key specified` on
8), but three are materially more informative than the replacement and
are worth listing in step 6's release note:
`parameter confirm is not set true` (3 rows),
`instance must specify at least one disk`, and
`cannot parse netblock: ...`.

## Step 2: which declarations are really required

Step 2's output, and the list the second gate reviews.

**One declaration moved.** `shared` on `ArtifactsEndpoint.post`
(`artifact.py:437`) is now `required=False`. Every other declaration in
the sweep table keeps `required=True`, unchanged.

### The rule, and the qualifier it carries

The step 2 brief states the rule mechanically -- `guarded` and `faults`
keep `required=True`, `accepted` becomes `required=False`. D35 states
it with a qualifier: "If the handler accepts the omission *and does
something sensible*, the declaration is wrong and gets corrected to
`required=False`." The six `accepted` rows are not alike on that
second clause, and the step is decided on it, which is also what the
sweep results section already concluded in prose ("only that last one
is a declaration step 2 should relax on the evidence here").

Four of the six answer 2xx and commit a broken operation: an agent
operation carrying a null `commandline` or `path`, or a DNS name
resolving to null. Nothing downstream of the API can do anything with
those but fail, at a point where the caller is no longer holding the
request. Publishing `required=false` for them would make that broken
acceptance the documented contract, and would deny step 3 the one
change that improves it -- turning a silent bad write into a 400 the
caller sees. Enforcing required-ness on a parameter the handler already
mishandles is not a contract regression; the request never worked.

Against that: an `accepted` verdict means some client could be omitting
the parameter today and getting a 200. D35's residual risk is exactly
this, and it is accepted here for the four, because what such a client
receives today is a queued operation that cannot succeed -- a 200 which
is not a success. The remaining case for relaxing them would be a
client that omits `path` and relies on the agent operation failing,
which is not a use.

### The six, one at a time

| Endpoint | Parameter | Decision | Why |
|----------|-----------|----------|-----|
| `ArtifactsEndpoint.post` | `shared` | **`required=False`** | The handler's signature is `shared=False` and the omission creates an unshared artifact, which is the documented default behaviour and what every client that never passes `shared` already relies on. A real optional parameter, declared wrongly. The only row where D35's "does something sensible" is satisfied. |
| `InstanceAgentExecuteEndpoint.post` | `command_line` | keeps `required=True` | Queues an `execute` operation whose `commandline` is null (`instance.py:2097`). The agent has no command to run; the operation exists only to fail. |
| `InstanceAgentGetEndpoint.post` | `path` | keeps `required=True` | Queues a `get-file` whose `path` is null (`instance.py:2032`). No file is named, so nothing can be fetched. |
| `InstanceAgentPutEndpoint.post` | `path` | keeps `required=True` | Queues `put-blob` and `chmod` whose `path` is null (`instance.py:1961`), after the blob has been resolved -- so a real blob is queued for delivery to nowhere. |
| `NetworkDNSAddressEndpoint.post` | `value` | keeps `required=True` | Stores `hosteddns[name] = None` and enqueues the dnsmasq rewrite (`network.py:765`). The record is written and the network reconfigured around an address that does not exist. |
| `UploadDataEndpoint.post` | `body` | keeps `required=True`, and it is inert | The raw-body marker. `compile_parameters()` sets `raw_body` and `continue`s before `required_names` is touched (`validation.py:228`), so this declaration cannot produce a missing-required finding whatever its fifth element says, and step 3's enforcement will never reach it. There is also no such thing as omitting a request body -- the sweep's "omission" is a zero-length one -- so the published `required: true` is not a false claim. Left exactly as it is. |

The four kept rows carry a one-line comment at the declaration saying
the handler does not refuse the omission today, because the next author
to read that tuple will not have this plan open.

### What this costs elsewhere

* **The published specification changes by one line.** `shared` leaves
  the `required` array of the `POST /artifacts` body schema. The
  parameter-level `required: true` on the body object itself is
  unchanged, because `url` is still required. Nothing else in the
  document moves, and `test_openapi_spec.py` needed no new
  `STRUCTURED_PARAMETERS` entry: that table is about structured and
  bounded types, and `shared` is a plain boolean.
* **The sweep keeps measuring all 76.** `required_declarations()` is
  derived from the declarations, so a relaxed one would otherwise drop
  out of the census and take its verdict out of the pinned table with
  it. `RELAXED_BY_STEP_2` in `test_required_sweep.py` names what step 2
  moved and keeps it enumerated, and a new test pins the split in both
  directions -- so neither a quiet revert of `shared` nor a later
  mechanical sweep of the four kept rows can happen without that file
  saying so. No verdict changed; no handler was touched.
* **The census script in the appendix now prints 75, not 76.** It knows
  nothing about `RELAXED_BY_STEP_2`, and one declaration it used to
  count is no longer declared required. Definition-of-done item 1 is a
  statement about step 1's deliverable and is unaffected; a reader
  re-running the script after step 2 should expect 75.
* **`test_request_validation.py` lost its example.**
  `test_an_omitted_required_parameter_still_reaches_the_handler` used
  `shared` precisely because it was declared required and optional in
  fact. It is repointed at `command_line` on the agent execute route,
  which is a sharper example of the same property and asserts the null
  `commandline` the omission queues.

### Definition of done item 3

Item 3 as written -- "every declaration still carrying `required=True`
in a `body` or `query` location has a row in the sweep table whose
verdict is not `accepted`" -- is the mechanical rule, and this step
does not satisfy it: four `accepted` rows deliberately keep
`required=True`. It was written before the sweep existed, when
`accepted` was expected to mean "the handler copes". It does not mean
that in four cases out of six. The item should read: every declaration
still carrying `required=True` in a `body` or `query` location has a
row in the sweep table, and any row whose verdict is `accepted` has a
recorded reason in this section for staying required.

### Messages and statuses enforcement will change

For step 6's release note, and the Risks section's instruction to
count them. After step 3, an omitted required parameter answers
`400 <parameter>: declared required but not supplied`. 65 rows are
already a 4xx, and on 57 of them the existing message is a bare
restatement of the same fact (`no value specified` on 14 rows, `no key
specified` on 8, `missing required field(s): X` on 10, `no X
specified` on most of the rest), so the replacement loses nothing.

Materially worse, all of them only on the pure-omission path:

| Request | Today | After step 3 |
|---------|-------|--------------|
| `DELETE /artifacts`, `/instances`, `/networks` with no `confirm` | `400 parameter confirm is not set true` | `400 confirm: declared required but not supplied` |
| `POST /instances` with no `disk` | `400 instance must specify at least one disk` | `400 disk: declared required but not supplied` |
| `POST /instances/<i>/interfaces` with no `network` | `400 network specification should contain JSON objects` | `400 network: declared required but not supplied` |

Each of those three messages survives for the case that actually
carries the information: `confirm: false`, `disk: []` and a malformed
networkspec all still reach the handler and still get the specific
message. Only the omission, where the specific message was not telling
the caller anything the generic one does not, changes. That is five
rows, and on the Risks section's threshold ("more than two or three is
an argument to revisit") it is worth the gate looking at, but three of
the five are the same message on three routes.

A status code change, which is not a message change and is worth the
release note stating separately:

| Request | Today | After step 3 |
|---------|-------|--------------|
| `POST`/`DELETE /networks/<n>/dns` with no `name` | `406 invalid DNS name` | `400 name: declared required but not supplied` |

And two improvements, recorded so the release note is not one-sided:
`POST /networks` with no `netblock` currently answers `400 cannot parse
netblock: None does not appear to be an IPv4 or IPv6 network`, which
tells the caller the server saw a `None` it should never have been
sent; and every one of the 5 remaining `faults` rows stops being a 500
with an exception record and becomes a 400 naming the parameter. The two
faults the review round fixed are a larger improvement still, because
they were 500s on paths a caller reaches without omitting anything.

## Step 4: how wide each format really is

F3's set of thirteen is unchanged -- re-derived from `ARGTYPES` and
`declarations.handlers()` rather than from the table's line numbers,
which drifted by up to sixteen lines when step 2 added comments to the
declarations it touched. The same thirteen endpoints, methods and
parameter names; `uuidorname` still has no body or query use at all,
and `macaddr` still has none.

The step's whole risk is a validator stricter than its handler, so
what each handler accepts *today* was established by reading it, and
by reading what the functional suite and the shipped client send. The
answers, and what each validator does with them:

| Declaration | What the handler accepts today | Validator |
|---|---|---|
| `InstancesEndpoint.post.user_data` | whatever `base64.b64decode()` decodes at the config drive (`instance.py:1930`), which in practice means base64 wrapped at 76 columns | `b64decode(''.join(value.split()), validate=True)` |
| `NetworksEndpoint.post.netblock` | whatever `ipaddress.ip_network()` parses; the handler already 400s on a `ValueError` | `ip_network(value)` |
| `NetworkDNSAddressEndpoint.post.value` | anything at all -- the handler never looks at it, and it is rendered straight into dnsmasq's hosts file by `dnshosts.tmpl` | `ip_address(value)` |
| `ArtifactsEndpoint.post.url` | a URL **or** an image shortcut with no scheme: `cirros`, `cirros:0.4.0`, `ubuntu:20.04`, expanded by `images._resolve_image()` | `urlparse(value)` only |
| `ArtifactUploadEndpoint.post.source_url` | anything; it is the URL the artifact *claims*, and its own default is `sf://upload/<ns>/<name>` | `urlparse(value)` only |
| `InstancesEndpoint.post.nvram_template` | `label:<name>`, `sf://blob/<uuid>`, or anything else passed through to `Blob.from_db()` | `urlparse(value)` only |
| `AuthIssuersEndpoint.post.jwks_uri`, `AuthIssuerEndpoint.put.jwks_uri` | only `https://...`, enforced by `_validate_issuer_arguments()` in the handler | `urlparse(value)` only |
| `ArtifactUploadEndpoint.post.upload_uuid`, `.blob_uuid`; `InstanceAgentPutEndpoint.post.blob_uuid` | any string; a miss in the following `from_db()` is a 404 | `uuid.UUID(value)` |
| `LabelEndpoint.post.blob_uuid` | any string, unchecked: it goes to `Artifact.add_index()` and the route answers 200 with a label version pointing at a blob that does not exist | `uuid.UUID(value)` |
| `ClusterOperationsEndpoint.get.target_uuid` | any string; a miss in `cls.from_db()` is a 404 | `uuid.UUID(value)` |

Three of those readings changed what the step would otherwise have
built.

**`base64` strips whitespace and then validates strictly.** The step
first wrote this as a lenient `b64decode(value)`, reasoning that
`validate=True` refuses base64 wrapped at 76 columns -- which is what
`base64 user-data.yaml` emits and therefore what a caller running
`sf-client instance create -U "$(base64 user-data.yaml)"` sends, and
which the config drive decodes today. That reasoning is half right and
the conclusion was wrong, and the review of this phase caught it.
b64decode's default does not merely tolerate the newlines in a wrapped
value: it discards *every* character outside the alphabet, so it throws
away the hashes, colons and spaces of raw cloud-config too and decodes
whatever alphabet characters are left. Whether a raw payload is refused
is then a coin flip on the filtered length modulo 4. Measured over five
realistic raw cloud-config samples, two passed -- so the lenient
validator left most of #3269 open.

Stripping the whitespace *before* a strict decode gets both properties
at once: a wrapped value survives the strip and decodes, and raw
cloud-config reaches a strict decoder with its non-alphabet characters
intact and is refused deterministically. All five samples are refused,
and the wrapped output of `base64(1)` is still accepted. Three tests
pin it: `test_byte_takes_what_the_config_drive_decodes` and
`test_wrapped_base64_user_data_still_reaches_placement` hold the
wrapped half, and
`test_byte_refuses_raw_config_the_lenient_decode_accepted` carries the
two samples which used to pass, asserting first that the lenient decode
accepts them so the sample cannot silently stop demonstrating the
hole.

**`url` cannot require a scheme, and so barely validates.** Of the
five declarations it covers, only `jwks_uri` is a web URL, and the
handler already enforces `https://` there. The other four take
scheme-less shortcuts (`cirros`), non-hierarchical schemes
(`label:mylabel`) and Shaken Fist's own `sf://` family. What is left
that is still a check is "urllib.parse can parse it", which in
practice refuses an unbalanced bracket in the authority and nothing
else. That is thin, and it is recorded here as thin rather than
dressed up: the alternative considered was leaving `url` out of
`_FORMATS` entirely, which the step brief permits. It is in because
the rule it enforces is the stdlib's own definition of "not a URL" and
costs nothing, not because it closes anything.

**`target_uuid` is safe for a reason that had to be checked.** It is
the one `uuid` declaration that could have been carrying a name, since
`namespace` and `node` are keyed by name rather than uuid and
`get_object_class()` resolves both. It is not: every object type a
cluster operation can target comes from a `target_fields` declaration
in `shakenfist/schema/operations/`, and the complete set is `ARTIFACT`,
`INSTANCE`, `BLOB`, `NETWORK`, `INTERFACE` and `AGENTOPERATION`.

Two smaller notes. `ipv4` compiles to `ipaddress.ip_address()` rather
than `IPv4Address`, so it accepts IPv6 as well as the published format
promises -- the value's only consumer is a hosts file, which takes
either, and enforcing narrower than the server is the failure this
step exists to avoid. And `netblock` accepts a bare address, because
`ip_network('10.0.0.0')` is a /32: the handler takes it and then
refuses it for being below the minimum size of /29, which is a policy
about how small a network may be and stays in the handler (D34).

Statuses this changes, in addition to the messages table above: a
non-uuid `blob_uuid`, `upload_uuid` or `target_uuid` that used to
answer 404 from a `from_db()` miss now answers 400 from the validation
layer. Both are refusals of a string that could never have named an
object, and `warn` and `off` restore the 404.

## Decisions

**D32. The declarations get corrected before required-ness is
enforced, not after.** F1 says all 75 are optional in fact, which means
the declarations and the handlers disagree in every single case — so
"enforce what is declared" would be enforcing something nobody has
checked. Step 1 classifies each parameter by what the handler actually
does with the omission, and step 2 rewrites the declarations to match.
Only then does enforcement turn on. This is the same shape as phases
3→4: fix the declarations the measurement found wrong, *then* reject.

**D33. Semantic validators attach to the published `format` string,
not to a parallel table.** `_field()` grows a `_FORMATS` mapping keyed
on the exact string `ARGTYPES` renders, so what the OpenAPI document
promises and what the server enforces are the same string by
construction. This is the argument `ANY_VALUE_FORMAT` already makes in
its own comment, generalised. A validator rather than a pattern,
because the netblock comment in `base.py:409` is right that a CIDR
regex would describe the API as narrower than `ipaddress.ip_network()`
makes it — a function can call `ip_network()` and be exactly as wide as
the server.

**D34. Handler guards stay, even where enforcement makes them
unreachable.** `warn` and `off` are the operator's rollback and hand
the handler whatever the caller sent, so a guard deleted as "dead" is a
500 waiting for the next rollback. Phase 5 established this precedent
deliberately in `InstancesEndpoint.post`, where the `isinstance(name,
str)` arm is documented as unreachable under `enforce` and kept anyway.
The new guard for #323 is written to the same standard, and #4183's
MAC guard already meets it — it runs in the handler, so it holds in
every mode.

**D35. No warn window for required-ness.** This is the departure from
the phase 3→4 pattern and the decision most likely to be argued with.

The case for a window: enforcing required is the change the master plan
itself flags as most likely to break working clients, and every
previous tightening in this plan was measured before it was turned on.

The case against, which this plan takes: a warn window measures which
omissions *happen*, and the question is which omissions the server
*accepts*. F2 is the evidence — 26 days of production produced 20
missing-required findings across 2 routes, every one on a request that
already answered 400, leaving 73 of the 75 declarations untouched by
any traffic at all. Step 1's sweep exercises all 75 deterministically
and answers the question directly. Measurement by traffic is the weaker
instrument here, and waiting for it would defer the phase by weeks to
learn less.

The residual risk is a caller that omits a parameter the sweep judges
required. Step 1 resolves that class *toward* the caller: a parameter
is only marked required if the handler refuses the request without it
today. If the handler accepts the omission and does something sensible,
the declaration is wrong and gets corrected to `required=False` —
which also removes a false claim from the published OpenAPI.

**D36. The sweep drives real requests, not the AST.** #4167's closing
note asks for this by name, and phase 5's F5 is why. The apparatus
already exists: `AuthenticatedStackTestCase` in
`test_request_validation.py` drives authenticated requests through the
whole decorator stack, which is what caught the difference between a
handler tested in isolation and the deployed behaviour in phase 3's
review.

**D37. `missing-required` keeps its own finding reason after
enforcement.** It would be tidier to fold it into the enforceable set
and delete the filter, but the reason code is what makes the 20 events
in F2 legible, and an operator rolling back to `warn` needs to see
required failures distinguished from type failures. The filter goes;
the reason code stays.

**D38. #528 does not get a partial answer here.** Declaring a
diskspec's shape without also handling the resolution logic tangled
through it would leave two validation paths for one parameter, which is
the state this whole plan exists to end. Phase 7 takes it whole.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | xhigh | opus | worktree | **Classify all 75.** Write `shakenfist/tests/external_api/test_required_sweep.py`, driving a real request per declaration through `AuthenticatedStackTestCase` (`test_request_validation.py`; `test_instance_create_validation.py` is the worked example, including its `mock.patch.object(instance_api, 'SCHEDULER', None)` containment — copy it, an escaped Scheduler breaks unrelated tests with a 507). Enumerate the 75 from `declarations.handlers()`, not by hand; the census script is the appendix below and its `_defaults()` is the shape to reuse. For each, send an otherwise-valid body with that one parameter omitted, and record the status and whether `record_exception` was called. Emit a table into `docs/plans/PLAN-api-input-validation-phase-06-required.md` under *Sweep results* with one row per parameter and a verdict of `guarded` (handler answers 4xx), `faults` (5xx or an exception record) or `accepted` (2xx/3xx). Do **not** change any declaration in this step. Commit subject: `Ask every handler what an omission does.` |
| 2 | high | opus | none | **Correct the declarations.** Using step 1's table: `guarded` and `faults` keep `required=True`; `accepted` becomes `required=False`, with a one-line comment on any that is surprising. Touch only the fifth element of the declaration tuples. `tools/fix-api-parameter-locations.py` does not do this, so it is by hand; `test_parameter_declarations.py` and `test_openapi_spec.py` must both stay green, and the published specification changes, so expect `test_openapi_spec.py`'s expectations to need updating. Commit subject: `Say which parameters are really required.` |
| 3 | medium | sonnet | none | **Enforce it.** Delete the `MISSING_REQUIRED` filter at `shakenfist/external_api/base.py:1906` so a missing-required finding is enforceable like any other, keeping the reason code (D37). Rewrite the `CompiledEndpoint` docstring at `validation.py:82` and the `validate_request` docstring at `base.py:1817`, both of which say required-ness is recorded and never enforced and both of which name phase 6 as the decider. Add tests that an omitted required parameter answers 400 naming the parameter, that an explicit JSON `null` does the same, and that under `warn` it answers whatever it answered before. Commit subject: `Refuse a request missing a required parameter.` |
| 4 | high | opus | none | **Make the type tokens mean something.** Add a `_FORMATS` table to `validation.py` keyed on the exact `format` strings in `ARGTYPES` (`byte`, `a CIDR netblock`, `an IPv4 address as a string`, `url`, `uuid`), each mapping to a validator function, and consult it in `_field()` alongside the existing `pattern` handling. Use `base64.b64decode(..., validate=True)`, `ipaddress.ip_network()`, `ipaddress.ip_address()`, `urllib.parse.urlparse()` and `uuid.UUID()` rather than regexes — D33 explains why. Every validator must be a no-op on `None` (fields are `allow_none=True` and required-ness is step 3's business, not this one's) and must raise `marshmallow.ValidationError`, never let a library exception escape. The 13 affected declarations are tabulated in F3; check each still accepts what its handler accepts today, particularly `nvram_template` and `source_url`, which may carry scheme forms a strict URL parser would refuse. This closes [#3269](https://github.com/shakenfist/shakenfist/issues/3269). Commit subject: `Enforce the formats the API publishes.` |
| 5 | medium | sonnet | none | **Guard the one that no schema can reach.** In `NetworksEndpoint.post` (`network.py:42`), after the existing `/29` check, refuse a netblock overlapping the floating network — read it the way `network.floating_network()` does at `network.py:707`, and answer 400 naming the conflict; if no floating network is configured, do not refuse. It keeps working under `warn`/`off` (D34). Closes [#323](https://github.com/shakenfist/shakenfist/issues/323). Unit tests, plus a cluster CI case for the overlap in `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/`. The MAC guard this step also briefed landed in #4183 (see *Amendments*); `_netdesc_safety_checks` already refuses a malformed `macaddress` at `instance.py:347`, so do not add a second check — if anything here needs the published pattern, use `util_network.MACADDR_PATTERN`. Commit subject: `Refuse an overlapping netblock.` |
| 6 | medium | sonnet | none | **Documentation and close-out.** `docs/developer_guide/writing_an_endpoint.md` currently tells authors required-ness is not enforced — correct it, and say what a `format` token now costs them. Add a v07-v08 release note entry covering the required-ness change, the format enforcement, and the new netblock guard, listing any declaration step 2 moved to `required=False` that a caller could notice. Update the master plan's Execution table, the phase split from F7, and the `docs/plans/index.md` row. Commit subject: `Document what required now means.` |

## Risks and mitigations

**A parameter marked required that a real client omits.** The one that
matters. Mitigated by D35's rule — required only where the handler
refuses today — so a client omitting it is already failing. The
reviewer's specific job (stated in the review effort note) is to look
for a counter-example. Residual: a client that omits a parameter and
*relies* on the resulting 4xx changing shape. Accepted.

**The sweep repeats phase 5's F5.** The same family of analysis over the
same declarations, and it got the wrong answer last time by asking a
well-formed question about the wrong property. Mitigated by D36 —
driving real requests answers "what does the server do" directly rather
than by inference — and by step 1 producing a table the management
session can spot-check by hand against three handlers it picks.

**Enforcement degrades a better error message.** `POST /instances` with
no `disk` answers `instance must specify at least one disk` today and
would answer `disk: declared required but not supplied`. Mitigated
weakly: the parameter is named either way and the shape is unchanged
(D4). Step 2 should note any message it judges materially worse, and
step 6 should list them; if there are more than two or three, that is
an argument to revisit.

**A format validator is stricter than the handler.** `url` in
particular: `urlparse()` accepts almost anything, but a validator that
demands a scheme would refuse `nvram_template` values the handler
accepts. Mitigated by step 4's instruction to check each of the 13
against its handler, and by the functional suite, which creates
artifacts from URLs.

**The published OpenAPI changes shape.** Moving declarations to
`required=False` changes the specification, which `test_openapi_spec.py`
validates and which downstream consumers read. Mitigated: this is a
correction, not a regression — the specification currently claims things
the server does not require. Step 6's release note says so explicitly.

## Definition of done

1. `docs/plans/PLAN-api-input-validation-phase-06-required.md` carries a
   *Sweep results* table with one row for each of the 75 declarations
   from F1, each with a verdict of `guarded`, `faults` or `accepted`,
   and the count of rows equals the count the census script reports.
2. `grep -n "MISSING_REQUIRED" shakenfist/external_api/base.py` shows no
   line that filters it out of an enforceable set.
3. Every declaration still carrying `required=True` in a `body` or
   `query` location has a row in the sweep table, and any row whose
   verdict is `accepted` has a recorded reason for staying required.
   (Amended by step 2, which found four `accepted` rows where the
   handler accepts the omission and commits a broken operation, so
   D35's "does something sensible" qualifier keeps them required. The
   original wording, which said no `accepted` row may stay required, is
   the mechanical rule without that qualifier -- see *Step 2: which
   declarations are really required*.)
4. A test proves that omitting a required body parameter answers 400
   naming that parameter, and that sending it as an explicit JSON `null`
   answers the same; both mutation-tested by restoring the filter from
   item 2, and both failing when it is restored.
5. A test proves that under `API_VALIDATION_MODE=warn` an omitted
   required parameter answers what it answered before this phase, for
   at least one `guarded` and one `faults` parameter.
6. `POST /instances` with a `user_data` that is not valid base64 answers
   400, and `shakenfist/instance.py:1930` is never reached with it —
   pinned by a test, not by reading.
7. Each of the five format validators has a test for a value it accepts
   and a value it refuses, and a test that `None` passes through
   untouched.
8. `POST /networks` with a netblock overlapping the configured floating
   network answers 400, and with no floating network configured answers
   what it answers today.
9. #3269 and #323 are closed, each closing comment naming the commit
   and the check that closed it. (#534 was closed by #4183 before this
   phase started; a networkspec carrying `"macaddress": "not-a-mac"`
   already answers 400 rather than reaching
   `shakenfist/network/interface.py:143`, and
   `shakenfist/tests/test_macaddr_validation.py` holds it there.)
10. No page states required-ness differently: the
    `CompiledEndpoint` docstring, the `validate_request` docstring,
    `docs/developer_guide/writing_an_endpoint.md` and the v07-v08
    release note all say it is enforced.
11. The master plan's Execution table has nine rows, phase 7 is
    *Structured parameter schemas*, phase 8 is the push audit, and the
    `docs/plans/index.md` row reads `6 of 9`.
12. `pre-commit run --all-files` is clean.

### Definition-of-done audit (step 6)

1. **Met.** The *Sweep results* table above carries 76 rows (65
   `guarded`, 5 `faults`, 6 `accepted` after the review round; 63/7/6
   as step 1 measured them), matching what
   `test_the_census_still_finds_seventy_six` and `test_the_sweep` pin
   against `required_declarations()`/`SWEEP` in
   `shakenfist/tests/external_api/test_required_sweep.py`. The item's
   own wording says "75 declarations from F1"; F1's own correction
   (*What the sweep says that F1 did not*) already notes the true count
   is 76 (75 defaultable plus the raw-body marker), so the table is
   right and the item's prose is the thing carrying the stale number,
   not the deliverable.
2. **Met.** `grep -n "MISSING_REQUIRED" shakenfist/external_api/base.py`
   returns nothing at all now — not merely no *filtering* line, the
   name is not present in the file.
3. **Met, as amended.** `shared` on `POST /artifacts` is `required=False`
   (`shakenfist/external_api/artifact.py:437`, with the comment step 2
   added). The four other `accepted` rows
   (`InstanceAgentExecuteEndpoint.post.command_line`,
   `InstanceAgentGetEndpoint.post.path`,
   `InstanceAgentPutEndpoint.post.path`,
   `NetworkDNSAddressEndpoint.post.value`) keep `required=True` with
   the reasoning recorded in *Step 2: which declarations are really
   required*, and `test_step_2_relaxed_exactly_what_it_said_it_did`
   pins the split in both directions.
4. **Met**, with one caveat on how it was checked. The omission and
   explicit-`null` tests
   (`test_enforce_mode_rejects_missing_required`,
   `test_enforce_mode_rejects_an_explicit_null_the_same_way` in
   `test_request_validation.py`) pass today, and by inspection the
   filter step 3 deleted is exactly what stood between them and a
   fall-through to the handler — restoring the
   `[f for f in findings if f.reason != validation.MISSING_REQUIRED]`
   guard makes both a no-op path when the *only* finding is
   missing-required, so both tests would fail against it. I did not
   re-run that reversion live in this sandbox (a mid-session policy
   restriction blocked writing a temporary revert to production code
   from this documentation-only step), so this is verified by reading
   the diff rather than by executing the mutation myself; the step 3
   commit message records that the author did run it.
5. **Met.** `RequiredSweepTestCase` in `test_required_sweep.py` now
   runs at `mode = 'warn'` and asserts, for all 76 rows including the
   5 `faults` and 6 `accepted` populations, that the response is
   unchanged from the published `SWEEP` table — not just the two the
   step brief named.
   `test_warn_mode_still_answers_the_handlers_own_message` in
   `test_request_validation.py` is the single readable pin for the
   `guarded` case.
6. **Met.** `test_unencoded_user_data_is_refused_before_the_hypervisor`
   drives a real `POST /instances` with unencoded cloud-config,
   asserts 400 naming `user_data`, and asserts `Instance.new` was never
   called — the only path to `instance.py:1930`.
7. **Met.** Each of the five validators in
   `shakenfist/tests/external_api/test_format_validation.py` has an
   accept test, a refuse test, and shares
   `test_every_validator_passes_none_through_untouched`, which checks
   all five in one pass.
8. **Met.** `NetworkCreateFloatingOverlapTestCase` in
   `test_network.py` covers identical, contains, contained-by and
   no-floating-network-configured cases, plus
   `test_a_partial_overlap_cannot_be_expressed` recording why no fifth
   case exists.
9. **Not yet met, deliberately.** Per this step's brief I have not
   closed either issue. #3269 and #323 both carried `Fixes #NNNN` in
   their commits and auto-closed on merge. This item originally named
   those commits as `03ea26514` and `d6b84b365`, which is what they
   were before #4199 was rebased onto `develop` shortly before it
   merged; those objects exist in no clone but the one that wrote them.
   They landed as `ec406a78a` and `c7a432886`. #4222 corrected the same
   pair in the master plan and did not reach this file; phase 8's
   survey found it here. A SHA recorded while a branch is in flight is
   a SHA a rebase can invalidate — read them off the first-parent range
   after the merge.

   The commit and the check that proves each is named in my
   final report and in the master plan's *Where the tracked issues
   stand* section, for the management session to post as closing
   comments. #534 was already closed by #4183 before this phase
   started, as the item itself notes.
10. **Met.** `CompiledEndpoint`'s and `validate_request`'s docstrings
    were rewritten in step 3;
    `docs/developer_guide/writing_an_endpoint.md`'s required-ness
    paragraph and `docs/release_notes/v07-v08.md` were corrected in
    this step (two stale in-progress mentions of "required is recorded
    and never enforced" earlier in the release note were also updated
    to point at the new entry, rather than left standing as
    contradictions).
11. **Met, except the index number is different from the item's own
    wording.** The master plan's Execution table has nine rows, phase
    7 is *Structured parameter schemas*, phase 8 is *Push audit*. The
    item says the index row should read `6 of 9`, but that number was
    fixed as part of the *planning* commit before this phase's work
    started, when 6 phases (0-5) were complete; completing phase 6
    itself makes 7 phases complete, and every other "In progress" row
    in `docs/plans/index.md` counts completed phases, not the phase
    number in flight (compare `PLAN-scheduler-reservations.md`'s
    `11 of 14`, `PLAN-cluster-op-visibility.md`'s `2 of 7`). I have set
    it to `7 of 9` rather than the `6 of 9` the item names, per this
    step's own instruction not to leave the arithmetic unchanged when
    the completed-phase count genuinely changed.
12. **Met.** `pre-commit run --all-files` and the full `stestr` suite
    were run for this step; results are in the final report.

## Back brief

Before executing any step, back brief the operator on the understanding
of this plan and how the intended work aligns with it.

**Two gates, both before code moves:**

* **After step 1, before step 2.** The sweep table is the entire
  evidentiary basis for the phase, and step 2 acts on it irreversibly.
  Present the table, the counts by verdict, and any parameter whose
  verdict surprised the agent. The management session picks three rows
  and checks them by hand against the handlers before step 2 starts.
* **After step 2, before step 3.** Enforcement is the contract change.
  Present the list of declarations moved to `required=False` and the
  list still required, and confirm the split before the filter is
  deleted.

## Appendix: the census script

Run from a checkout root with `.tox/py3/bin/python`. It answers F1, and
step 1 enumerates its 75 rows rather than a hand-written list. Keeping
it here rather than in a scratch directory is deliberate: the number it
produces is the premise of D32 and D35, and a reviewer who cannot re-run
it has to take those on trust.

```python
import ast
import sys

sys.path.insert(0, '.')
from shakenfist.external_api import declarations


def _defaults(fn):
    """Parameter names of fn which have a default, so may be omitted."""
    args = fn.args
    positional = args.posonlyargs + args.args
    out = set()
    if args.defaults:
        for a in positional[-len(args.defaults):]:
            out.add(a.arg)
    for a, d in zip(args.kwonlyargs, args.kw_defaults):
        if d is not None:
            out.add(a.arg)
    return out, {a.arg for a in positional} | {a.arg for a in args.kwonlyargs}


rows = []
for _, _, cls, fn in declarations.handlers():
    have_default, accepted = _defaults(fn)
    for dec in fn.decorator_list:
        if 'swagger_helper' not in ast.unparse(dec):
            continue
        call = dec.args[0] if isinstance(dec, ast.Call) and dec.args else None
        if not (isinstance(call, ast.Call) and len(call.args) >= 3
                and isinstance(call.args[2], ast.List)):
            continue
        for item in call.args[2].elts:
            if not (isinstance(item, ast.Tuple) and len(item.elts) in (5, 6)):
                continue
            name = declarations.literal(item.elts[0])
            location = declarations.literal(item.elts[1])
            argtype = declarations.literal(item.elts[2])
            required = declarations.literal(item.elts[4])
            if location not in ('body', 'query') or required is not True:
                continue
            rows.append((cls.name, fn.name, name, location, argtype,
                         name in have_default, name in accepted))

print('declared required, body/query:', len(rows))
print('  handler gives it a default (omitting works today):',
      len([r for r in rows if r[5]]))
print('  no default (already effectively required):',
      len([r for r in rows if not r[5] and r[6]]))
print('  not a named handler kwarg:', len([r for r in rows if not r[6]]))
for r in sorted(r for r in rows if r[5]):
    print('  %-34s %-8s %-22s %s' % (r[0], r[1], r[2], r[4]))
```

As of `92b9caf12` this prints 76, 75, 0, 1. Ask it the other question --
which parameters lack a default -- and it prints zero, which is finding
F5 of phase 5 and is true and useless. The difference is one `not`.

## Progress

Step 1 done: the sweep is
`shakenfist/tests/external_api/test_required_sweep.py` and its table
is published above under *Sweep results*.

Step 2 done: one declaration moved to `required=False`, and the
reasoning for each of the six `accepted` rows is above under *Step 2:
which declarations are really required*.

Step 3 done: the `MISSING_REQUIRED` filter at `base.py:1914` is
deleted, so every finding is enforceable and the reason code survives
only as telemetry (D37). An explicit JSON `null` on a required
parameter is now treated the same as its absence (`validation.check()`),
which the `CompiledEndpoint` and `validate_request` docstrings no
longer contradict. `RequiredSweepTestCase` now runs at `warn` rather
than `enforce` -- an `enforce` run would answer the layer's own generic
refusal for every omission before any handler saw it, which is not the
question that file measures -- so its 76-row sweep is now also the
evidence for definition-of-done item 5, not merely the guarded/faults
pair the step brief names. Tests were added or corrected in
`test_request_validation.py`, `test_instance_create_validation.py`,
`test_auth.py` and `test_external_api.py` for every message the
*Messages and statuses enforcement will change* table above predicted.

Step 4 done: `validation._FORMATS` gives five published `format`
strings a validator apiece and `_field()` consults it, which closes
#3269. The width each validator was written to, and the three readings
that changed what it does, are above under *Step 4: how wide each
format really is*. `test_format_validation.py` holds each validator to
an accepted value, a refused value and a `None` passed through
untouched (item 7), and drives the unencoded-cloud-config body of
#3269 through a real `POST /instances` to prove the config drive
decode is never reached with it (item 6).
`test_validation_compiler.py`'s `format` test was inverted: it used to
assert that no `format` ever becomes a validator, and now names all
thirteen declarations that do.

Step 5 done: `NetworksEndpoint.post` refuses a netblock overlapping the
deployed floating network with a 400 naming both blocks, closing #323.
It reads the floating network the way `network.floating_network()`
does, without creating one, and falls back to `config.FLOATING_NETWORK`
when no floating network row exists yet -- the row wins when both
exist. `shakenfist/tests/external_api/test_network.py`'s
`NetworkCreateFloatingOverlapTestCase` covers the identical, contains,
contained-by and no-floating-network cases and records why a partial
overlap cannot be expressed (CIDR prefixes form a tree); a cluster CI
case was added to
`shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_networking.py`.
Only the floating network is guarded, as the plan scoped -- other
reserved ranges (node egress, mesh addressing) are untouched. The MAC
guard step 5 also briefed had already landed in #4183 before the phase
started (see *Amendments*); nothing further was added for it.

Step 6 done: this document, `docs/developer_guide/writing_an_endpoint.md`
and `docs/release_notes/v07-v08.md` all now say `required` is enforced,
the master plan's Execution table has nine rows with phase 6 marked
Complete, and the `docs/plans/index.md` row reads `7 of 9`. The phase is
complete; see *Definition of done*, below, for the item-by-item audit.

Review round done: the automated reviewer's findings on
[PR #4199](https://github.com/shakenfist/shakenfist/pull/4199) were
worked through and eleven of the twelve applied. What changed, and what
did not:

* **The base64 validator was wrong, and the plan overstated what it
  closed.** `b64decode` with its default arguments discards every
  character outside the alphabet, so it decoded raw cloud-config too
  whenever the filtered length happened to be a multiple of four -- two
  of five realistic samples. Stripping the whitespace and then decoding
  with `validate=True` refuses all five and still accepts the wrapped
  output of `base64(1)`. See *Step 4: how wide each format really is*.
* **The two bug rows in the sweep were fixed rather than pinned**
  ([#4194](https://github.com/shakenfist/shakenfist/issues/4194) and
  [#4195](https://github.com/shakenfist/shakenfist/issues/4195)), which
  moves the verdict table to 65 `guarded` and 5 `faults`. Both were
  faults on paths a caller reaches without omitting anything, and both
  contradicted the endpoint's own published response list. The
  reviewer's suggested one-token widening of `except ValueError` to
  `except (TypeError, ValueError)` was measured and does **not** close
  #4195: the symbolic fallback beneath it raises `AttributeError` on a
  null, so the 500 survives. An explicit type check is what closes it.
* **The overlap guard moved below the authorisation check**, so an
  unauthorised create gets its 401 rather than a 400 naming the
  cluster's floating netblock. The netblock parse stays above it: that
  is a pure input check and can answer anyone. The guard also no longer
  turns itself off when the floating network row exists but carries an
  empty netblock, and it now logs a warning when an unparseable
  configuration makes it fail open.
* **Functional coverage was added for both contract changes.**
  `cluster_ci_tests/test_api_validation.py` now drives a delete-all with
  no `confirm`, raw cloud-config as `user_data`, and -- the regression
  that matters -- a wrapped base64 `user_data` instance which must still
  boot. The last one is the only honest test of the format validator's
  width: the decode it stands in for happens on a hypervisor, in
  another daemon, from a config drive no unit test builds.
* **The sweep now reads declarations through
  `declarations.declarations()`.** It had grown its own copy of that AST
  walk to reach the type token, which `Declaration` did not carry;
  `test_parameter_declarations.py` had grown a second copy for the same
  reason. `argtype` is now a field on `Declaration` and both copies are
  gone, so there is one reading of a declaration tuple in the tree. The
  copies also silently skipped a declaration they could not read
  statically, where the shared helper returns a marker row --
  `required_declarations()` now raises on one by name, because a census
  which claims completeness must not skip what it could not parse.
* **`KNOWN_DEFECTS` was considered and not added.** The reviewer asked
  that the `faults` rows be labelled so a developer who fixes one gets a
  helpful message rather than "you broke something". The two rows that
  argument was about are the two fixed above. The five that remain are
  all a null reaching a null-hostile constructor on a path only an
  omission reaches; enforcement closes every one, and this file runs at
  `warn` to measure exactly that pre-enforcement behaviour. They record
  the rollback's honest answer, not an unfixed bug, so there is nothing
  for a label to point at. The reasoning is recorded above `SWEEP` where
  the next person to read the table will meet it.
