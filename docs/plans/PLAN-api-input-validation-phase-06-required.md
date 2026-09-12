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
* [#534](https://github.com/shakenfist/shakenfist/issues/534) — validate
  the caller-supplied MAC address format on interface create.

**Out:**

* **Element schemas for `dict` and `arrayofdict`.** This is the other
  half of #4167 and the whole of
  [#936](https://github.com/shakenfist/shakenfist/issues/936), and it is
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

### F5. #323 is not a format problem, and #534 is not a top-level one

Both need saying, because "semantic validators for #534, #3269, #323"
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
  `shakenfist/network/interface.py:143`. `_netdesc_safety_checks`
  (`instance.py:329`) validates `network_uuid` and the requested address
  range and never looks at `macaddress`. Because networkspecs are
  declared `dict`/`arrayofdict`, **no schema can reach it** — see F6. It
  is a handler guard too, for now.

### F6. Nested structures are unvalidated by construction

`dict` compiles to `fields.Dict()` and `arrayofdict` to
`fields.List(fields.Dict())`, neither carrying a value schema. Every
key inside a diskspec, networkspec or videospec is invisible to the
validation layer in every mode. This is the structural cause behind
#936, #534, and items 2 and 3 of #4167.

It is real, it is worth fixing, and it is not this phase. See below.

### F7. A phase split, and a correction to the master plan

The master plan's phase 6 row bundles required-ness with "semantic
validators for #534, #3269, #323, #936". The survey says #936 does not
belong with the others: the first three are guards and scalar
validators, and #936 is a vocabulary change — element schemas for
`dict` and `arrayofdict`, plus a decision about how much of
`InstancesEndpoint.post` can become declarative at all, given that
roughly 130 of its lines interleave validation with blob, label and
artifact *resolution* that no schema can express.

**This plan therefore splits phase 6 and renumbers the push audit.**
The master plan's Execution table and the `docs/plans/index.md` row are
corrected as part of the planning commit:

| Phase | Was | Now |
|-------|-----|-----|
| 6 | Required and semantics | Required and scalar semantics (this plan) |
| 7 | Push audit | **Structured parameter schemas** — #936, #534's declarative form, #4167 items 2 and 3 |
| 8 | — | Push audit |

The index arithmetic moves from `6 of 8` to `6 of 9`. The push audit
stays last by construction: it audits the accumulated diff of every
phase.

### Nothing else in the phase description was wrong

The `except TypeError` narrowing the row once described belongs to
phase 5 and is done. The three attribution issues it also listed are
closed.

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
New guards for #323 and #534 are written to the same standard.

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

**D38. #936 does not get a partial answer here.** Declaring a
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
| 5 | medium | sonnet | none | **Guard the two that no schema can reach.** In `NetworksEndpoint.post` (`network.py:42`), after the existing `/29` check, refuse a netblock overlapping the floating network — read it the way `network.floating_network()` does at `network.py:707`, and answer 400 naming the conflict; if no floating network is configured, do not refuse. In `_netdesc_safety_checks` (`instance.py:329`), refuse a `macaddress` that is not a MAC address, reusing `api_base.ARGTYPES['macaddr']['pattern']` so the guard and the published format are one string. Both keep working under `warn`/`off` (D34). Closes [#323](https://github.com/shakenfist/shakenfist/issues/323) and [#534](https://github.com/shakenfist/shakenfist/issues/534). Unit tests for both, plus a cluster CI case for the netblock overlap in `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/`. Commit subject: `Refuse an overlapping netblock and a bad MAC.` |
| 6 | medium | sonnet | none | **Documentation and close-out.** `docs/developer_guide/writing_an_endpoint.md` currently tells authors required-ness is not enforced — correct it, and say what a `format` token now costs them. Add a v07-v08 release note entry covering the required-ness change, the format enforcement, and the two new guards, listing any declaration step 2 moved to `required=False` that a caller could notice. Update the master plan's Execution table, the phase split from F7, and the `docs/plans/index.md` row. Commit subject: `Document what required now means.` |

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
   `query` location has a row in the sweep table whose verdict is not
   `accepted`.
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
9. A networkspec carrying `"macaddress": "not-a-mac"` answers 400 rather
   than reaching `shakenfist/network/interface.py:143`.
10. #3269, #323 and #534 are closed, each closing comment naming the
    commit and the check that closed it.
11. No page states required-ness differently: the
    `CompiledEndpoint` docstring, the `validate_request` docstring,
    `docs/developer_guide/writing_an_endpoint.md` and the v07-v08
    release note all say it is enforced.
12. The master plan's Execution table has nine rows, phase 7 is
    *Structured parameter schemas*, phase 8 is the push audit, and the
    `docs/plans/index.md` row reads `6 of 9`.
13. `pre-commit run --all-files` is clean.

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

Not started.
