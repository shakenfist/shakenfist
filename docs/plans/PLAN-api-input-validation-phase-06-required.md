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
through the whole decorator stack, in the default `enforce` mode. For
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
| `guarded` -- the handler answers 4xx | 63 |
| `faults` -- 5xx, or an exception was recorded | 7 |
| `accepted` -- 2xx | 6 |

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
| `InstanceAgentPutEndpoint` | post | `blob_uuid` | body | uuid | 500 | **faults** | the 404 is written `self.api_error(...)`, which does not exist (`instance.py:1955`) |
| `InstanceAgentPutEndpoint` | post | `mode` | body | string | 500 | **faults** | `int(None)` raises TypeError, and only ValueError is caught (`instance.py:1946`) |
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

**The 7 `faults` are the population #4167 describes, and two of them are
bugs independent of required-ness.**

* `InstanceAgentPutEndpoint.post` writes its blob refusal as
  `self.api_error(404, 'blob not found')` (`instance.py:1955`).
  `api_base.Resource` has no `api_error`, so *any* caller naming a blob
  which does not resolve -- not merely one who omitted the parameter --
  gets an `AttributeError`, a recorded exception and an opaque 500
  instead of the documented 404. Enforcing required-ness closes the
  omission path and leaves the bad-uuid path exactly as it is.
* The `mode` guard immediately above it is `try: int(mode) except
  ValueError`, and `int(None)` raises `TypeError`. This is the example
  `CompiledEndpoint`'s docstring cites as a parameter "declared
  required while omitting it has always been accepted"; that was true
  while `handle_authorization_exceptions` still caught `TypeError` and
  answered 400, and phase 5 deleted that arm (decision D23). It is now
  a recorded 500.

The other five faults are a null reaching a constructor: `Artifact.new()`
splits `source_url`, `Instance.new()` and `Network.new()` hand a null to
a pydantic model which refuses it, and `Artifact.add_index(None)` raises
`BlobMissing`. All five are closed by enforcing required-ness, which is
D32's argument restated in measurements.

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
with a generic one. 63 of 76 rows are already a 4xx whose message names
the problem, so step 3 will replace 63 hand-written messages with
`<parameter>: declared required but not supplied`. Most are already
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
`400 <parameter>: declared required but not supplied`. 63 rows are
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
sent; and every one of the 7 `faults` rows stops being a 500 with an
exception record and becomes a 400 naming the parameter.

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
Steps 4 to 6 not started.
