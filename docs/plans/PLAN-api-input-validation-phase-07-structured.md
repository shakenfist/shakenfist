# Phase 7: Structured parameter schemas

Phase 7 of [`PLAN-api-input-validation.md`](PLAN-api-input-validation.md),
following [phase 6](PLAN-api-input-validation-phase-06-required.md).

Phases 3 and 4 built a validation layer that compiles the published
OpenAPI specification into marshmallow fields and rejects anything the
specification does not describe. Phase 6 made the scalar tokens mean
something. All of it stops at the surface of a structure: a `dict`
compiles to `fields.Dict()` and an `arrayofdict` to
`fields.List(fields.Dict())`, neither carrying a value schema, so a
diskspec, a networkspec or a videospec is checked for being a mapping
and then handed to the handler unexamined. This phase teaches the
vocabulary to describe what is inside one.

**Planning effort:** high. The compiler change is small and the
declarations are few. Deciding what each spec's schema may *say* is
not: every key this phase constrains is a key some caller may be
sending today, and a schema narrower than the server is a breaking
change wearing the clothes of a correctness fix.

**Review effort:** high. A reviewer's job here is to find a key, a
value or a type that this phase's schemas refuse and some working
caller sends. The second job is to check that each published `enum`
was read off the code that consumes the value and not off the
documentation that describes it.

## Context

The compiler already recurses. `_field()`
(`shakenfist/external_api/validation.py:400`) reads a rendered schema
fragment and builds a marshmallow field from it, and its `array`
branch calls itself:

```python
if declared == 'array':
    return fields.List(_field(spec.get('items', {})), **kwargs)
```

What it has no branch for is an object with `properties`. `_SCALARS`
(`validation.py:86`) maps `'object'` to `fields.Dict`, which accepts
any mapping whatsoever, and `ARGTYPES['dict']` (`base.py:400`) renders
`{'type': 'object'}` with nothing inside it. So the recursion exists,
the vocabulary is the thing that has nothing to recurse into.

That matters for how this phase is built. The module's design rule,
stated in three separate comments in `validation.py`, is that the
published specification and the enforced check must be the same string
by construction — `ANY_VALUE_FORMAT` and `_FORMATS` are both keyed on
exactly what `ARGTYPES` renders, precisely so that a token cannot
render as one thing and compile as another. A structured token obeys
the same rule: it renders `properties` into the published OpenAPI, and
the compiler builds its schema by reading them back.

## Scope

**In:**

* An object branch in `_field()`, so a rendered `properties` block
  compiles to a nested marshmallow schema.
* Structured type tokens for the three specs the API actually
  publishes — diskspec, networkspec, videospec — in both their
  single-object and array forms.
* The six declarations that carry them (enumerated in F2 below).
* [#528](https://github.com/shakenfist/shakenfist/issues/528), which
  since 2026-09-12 is also the tracker for what
  [#936](https://github.com/shakenfist/shakenfist/issues/936)
  described. See F9: the issue's *text* describes different work, and
  fixing that is part of this phase.
* Items 2 and 3 of
  [#4167](https://github.com/shakenfist/shakenfist/issues/4167) — the
  nested half. Its top-level half was phase 6's.
* The nested half of
  [#3612](https://github.com/shakenfist/shakenfist/issues/3612), the
  mechanism issue: a wrong-typed value one level down is the same
  defect as a wrong-typed value at the top, and phases 3 to 6 only
  closed the top.
* Integer strictness (F8). A check-only field that accepts `1.5` as an
  integer and hands the handler `1.5` is not a check.

**Out:**

* `metadata` on `POST /instances`, and the `value` parameters of every
  metadata endpoint. Their keys are the caller's and their values are
  deliberately uninterpreted; `ARGTYPES['any']`'s own comment records
  why. See F4.
* `bound_claims` on the mapping-rule endpoints. Already completely
  guarded by hand, better than a schema could be. See F3.
* [#4223](https://github.com/shakenfist/shakenfist/issues/4223), the
  null-reference lookup bug this phase's survey found. Filed, not
  fixed here. See F7.
* The remaining `uefi` / `secure_boot` half of #4167. They are
  `required=False` booleans, so phase 6's required-ness enforcement
  does not reach them and no element schema contains them. #4167 stays
  open carrying them.
* Response validation, which decision D7 put out of scope for the
  whole plan.
* Making `InstancesEndpoint.post` declarative beyond its parameter
  types. Roughly 130 of its lines interleave validation with blob,
  label and artifact *resolution*, which no schema expresses. F7 of
  the phase 6 plan said this and it is still true.

## What the survey found

The master plan's phase 7 row is accurate in every particular: `dict`
does compile to `fields.Dict()`, `arrayofdict` does compile to
`fields.List(fields.Dict())`, neither does carry a value schema, and
the keys inside a diskspec, networkspec or videospec are unvalidated in
every mode. Everything below is detail the row could not have had.

The survey was run by sending requests, not by reading — phase 6's
decision D36 and the lesson of phase 5's finding F5. A probe subclassed
`RequiredSweepTestCase` from
`shakenfist/tests/external_api/test_required_sweep.py`, which already
carries a whole authenticated decorator stack and a working fixture,
and drove roughly sixty malformed requests through it at the default
`API_VALIDATION_MODE=enforce`. Every status quoted below was measured.
The probe itself is in the appendix; it was not committed.

### F1. The outer shape is checked, and only the outer shape

`fields.Dict()` and `fields.List()` do reject the wrong container, and
the messages are already good:

| Sent | Answer |
|------|--------|
| `"disk": {"size": 8}` | 400 `disk: Not a valid list.` |
| `"disk": ["wombat"]` | 400 `disk[0]: Not a valid mapping type.` |
| `"video": [{"model": "vga"}]` | 400 `video: Not a valid mapping type.` |
| `"network": {"network_uuid": "..."}` | 400 `network: Not a valid list.` |
| `"side_channels": [5]` | 400 `side_channels[0]: Not a valid string.` |

That last row matters out of proportion to its size. `arrayofstring`
renders `{'type': 'array', 'items': {'type': 'string'}}` and the
existing `array` branch compiles the `items` fragment, so its elements
*are* validated, with an indexed message naming the offending element.
The machinery this phase needs already works; `arrayofdict` simply
renders `{'items': {'type': 'object'}}`, and an object with no
properties constrains nothing. The error shape a nested failure should
produce is therefore already established rather than invented here.

### F2. The structured population is six declarations, not a class

Every `dict` and `arrayofdict` declaration in the tree:

| Operation | Parameter | Token | Disposition |
|-----------|-----------|-------|-------------|
| `POST /instances` | `disk` | `arrayofdict` | diskspec — **in scope** |
| `POST /instances` | `network` | `arrayofdict` | networkspec — **in scope** |
| `POST /instances` | `video` | `dict` | videospec — **in scope** |
| `POST /instances/{ref}/interfaces` | `network` | `dict` | networkspec — **in scope** |
| `POST /instances` | `metadata` | `dict` | free-form, out — see F4 |
| `POST /auth/namespaces/{ns}/rules` | `bound_claims` | `dict` | guarded, out — see F3 |
| `PUT /auth/namespaces/{ns}/rules/{name}` | `bound_claims` | `dict` | guarded, out — see F3 |

Three shapes, four operations in scope. This is a much smaller phase
than "teach the vocabulary to carry element schemas" suggests, and the
smallness is the finding: the work is not a sweep, it is three schemas
written carefully.

All seven are already registered in `STRUCTURED_PARAMETERS`
(`shakenfist/tests/external_api/test_openapi_spec.py:104`), whose
completeness is derived from the published specification. Every entry
this phase changes has to be updated there in the same commit or
`test_every_published_structure_or_bound_is_registered` fails, which is
the mechanism working as designed.

### F3. `bound_claims` is already guarded, and better than a schema would be

Every malformed matcher is refused, by hand, with a message that names
the claim and the type:

| Sent as the matcher for claim `sub` | Answer |
|---|---|
| `5` | 400 `the matcher for claim "sub" must be a string or a list of strings, not a int` |
| `{"eq": "someone"}` | 400 `... not a dict` |
| `true` | 400 `... not a bool` |
| `null` | 400 `... not a NoneType` |
| `[1, 2]` | 400 `the matcher for claim "sub" must contain only non-empty strings` |
| `["a", 5]` | 400 same |
| `[["a"]]` | 400 same |
| `{}` (the whole mapping) | 400 `a rule must bind at least one claim, otherwise it accepts every identity the issuer will ...` |

This is exactly right, and it is right for a reason a schema cannot
reproduce: `federation.claim_matches` (`shakenfist/federation.py:347`)
matches a string against a string or a list of strings and returns
`False` for everything else *silently*, so a rule written with a
matcher of any other shape would be stored, would look like a grant,
and would never match an identity. The API refusing it at write time is
the only place that failure is visible. A `{"type": "object"}` with
free-form keys and a value schema of "string or array of string" would
say the same thing less precisely and would duplicate a guard whose
messages are better. Leave it, and record why.

It is also the proof of a thing worth stating plainly: the goal of this
phase is not to replace hand-written guards with declarations. It is to
stop the cases where there is no guard at all.

### F4. `metadata` is unconstrained on purpose

`_validate_instance_metadata` (`instance.py:1526`) is key-dependent:
`tags` must be a list, `affinity` must be a dict in one of two
recognised shapes, and everything else may be any non-empty JSON value.
The keys are the caller's. `ARGTYPES['any']`'s comment already records
that sfcbr's k3s traffic stores both a dict and a list under one
parameter. Nothing here is a phase 7 shape.

### F5. Eleven nested values are a recorded 500 today

Measured at `enforce`, every one writing an exception record and
answering the bare `server error` phase 5 left behind:

| Sent | Where it lands |
|------|----------------|
| `disk[].base` an int, a bool or a list | `AttributeError: 'int' object has no attribute 'lower'` in `util/general.py:noneish` |
| `disk[].size` a string or a dict | pydantic `ValidationError` from `InstanceData` inside `Instance.new` |
| `network[].network_uuid` an int, a dict or a bool | `AttributeError: 'int' object has no attribute 'replace'` in `util/general.py:valid_uuid4` |
| `network[].address` an int or a list | inside `n.ipam.is_in_range` |
| `network[].model` an int | pydantic, building the `NetworkInterface` |

Items 2 and 3 of #4167 are the first and third rows and are exactly as
filed, eleven months on. The second, fourth and fifth rows are new —
#4167 found two by inspection and stopped, which is the difference
between reading and sending.

The interface hotplug endpoint answers the same way for the netdesc
rows, from the same `_netdesc_safety_checks` and the same
`NetworkInterface` construction.

### F6. Nine more are accepted in silence

These reach placement, which in the probe's single-node fixture means a
507 — the same control the phase 6 sweep used, and proof the request
got past every guard in the handler. On the interface hotplug endpoint,
which has no scheduler in front of it, the equivalents answer **200 and
create the object**.

| Sent | Documented as | What happens |
|------|---------------|--------------|
| `disk[].size` `-5` | an integer size in GB | accepted |
| `disk[].size` `null`, `8.5`, `true` | as above | accepted |
| `disk[]` `{}` — no size, no base | a spec with at least one | accepted |
| `disk[].type` `"nonsense"` or `5` | enum: `disk`, `cdrom` | accepted |
| `disk[]` with an unknown key | four documented keys | accepted, key discarded |
| `network[].model` `"nonsense"` | enum of eight NIC models | accepted |
| `network[].float` `"yes"` | a boolean | accepted, and truthy, so it floats |
| `network[]` with an unknown key | five documented keys | accepted, key discarded |
| `video.model` / `.memory` / `.vdi`, any type at all | enum / integer / enum | accepted |

The videospec row is the starkest. `instance.py:833` checks that the
keys `model` and `memory` are *present* and defaults `vdi`, and checks
nothing else, so `{"model": 5, "memory": "lots", "vdi": 7}` is stored
verbatim on the instance. It surfaces much later and somewhere else:
`instance.py:1865` does `video.get('vdi', '').startswith('spice')`,
which is an `AttributeError` on a non-string, in the console endpoint
rather than in the create that accepted it.

The unknown-key rows are the original complaint. #936 was filed as
"silent failure on missing fields", and a caller who writes `siz`
instead of `size` gets a default-sized disk and no indication that
anything went wrong.

### F7. A null object reference resolves to an arbitrary object

The most serious thing the survey found, and it is not a vocabulary
bug. `Network.from_db_by_ref(None, namespace)`
(`shakenfist/network/network.py:189`) builds
`ObjectFilterCriteria(..., name=object_ref)`, and a `None` name reads
as *no name filter* rather than as a name of `None`. The query returns
every active network in the namespace, and one match is returned as
though the caller had named it.

`_netdesc_safety_checks` checks that `network_uuid` is *present*, not
that it has a value, so it walks through. End to end:

```
POST /instances/<ref>/interfaces  {"network": {"network_uuid": null}}
→ 200, and an interface is created on whichever network the namespace holds
```

With two networks in the namespace it raises `MultipleObjects` instead,
surfacing as `400 multiple networks have the name "None"`, which is at
least a refusal but is not a message anyone can act on.

The same shape is in `Instance.from_db_by_ref` (`instance.py:452`) and
`Artifact.from_db_by_ref` (`artifact.py:243`). Only the netdesc reaches
it, because every other `from_db_by_ref` call site in
`shakenfist/external_api/` is fed a route segment. The generic
`baseobject.from_db_by_ref` (`baseobject.py:394`) is correct, comparing
`o.name == object_ref` in Python where nothing has a name of `None`;
the defect arrived when the name match was pushed down to SQL for index
efficiency.

**Filed as [#4223](https://github.com/shakenfist/shakenfist/issues/4223)
and deliberately not fixed here.** A networkspec schema making
`network_uuid` a required string closes the only path a caller can
reach today, which would mask the defect at one call site and leave the
function wrong for the next one. The issue was filed carrying the
`automated-fix-attempted` label, which is the mitigation the master
plan has now recommended through two phases of being overtaken; this is
the first time it has been applied at filing rather than by the fixer.

### F8. The compiled path is check-only, so a coercing field is a lie

`"cpus": 1.5` answers 500. It passes validation, because marshmallow's
`fields.Integer` defaults to `strict=False` and `int(1.5)` succeeds,
and then reaches `InstanceData` unchanged and raises pydantic
`ValidationError`.

The reason it reaches the handler *unchanged* is decision D14's
check-only design: `validate_request` runs `schema.validate()` for its
findings and discards the deserialised result, so the handler always
sees the raw body. That is the right design — it is what keeps `warn`
and `off` honest — but it makes a non-strict numeric field actively
harmful rather than merely lax. The field says "this is a valid
integer" about a value that is not one and that nothing downstream will
convert.

It is a scalar problem, not a structured one, but it is in scope for
two reasons. It is one line and a test. And a nested integer — a
diskspec `size`, a videospec `memory` — would inherit the same lie the
moment this phase declares one, so fixing it afterwards would mean
revisiting the schemas this phase writes.

`fields.Float` has the same flag and does not need it: a number field
accepting an integer is not a lie, because JSON has one numeric type
and every integer is a valid float.

### F9. The issue this phase is filed under describes different work

#936 — *"Replace hand-rolled instance-create validation with a
declarative schema"*, whose residual reads "move to a declarative
marshmallow schema for completeness and to catch the remaining gaps" —
is this phase, precisely. It was closed as a duplicate of #528 on
2026-09-12, in a cleanup sweep of the oldest open issues, and the phase
6 plan recorded the move.

#528's own text, though, is *"Broaden declarative type/validity
checking across all API endpoints"*, whose residual reads "extend
schema coverage across the remaining endpoints. Only ~4 endpoint files
use `use_kwargs` typed schemas today". That is not this phase and it is
not open work: phase 3 compiled *every* declaration in the tree into a
schema and phase 4 turned rejection on, which covered every endpoint
without extending `use_kwargs` to any of them. Read literally, #528 was
closed by phase 4 and nobody noticed.

So the surviving tracker's text describes finished work while the
closed duplicate's text describes the work that remains. An
implementing agent sent to #528 reads a description of `use_kwargs`
coverage and either does the wrong thing or spends their first half
hour working out that they should not.

**Recommendation, as a step of this phase:** rewrite #528's body to the
element-schema scope, linking this plan, and note in it that the
`use_kwargs` residual was overtaken by phase 4. Reopening #936 instead
would undo a deliberate cleanup and leave two trackers again.

### Nothing else in the phase description was wrong

The claim that this is "a vocabulary change the size of phase 2" is the
one thing the survey would soften. Phase 2 added eleven tokens and
rewrote every body parameter's rendering. This adds five tokens, one
compiler branch and six declarations. The care required is comparable;
the surface area is not.

## Decisions

Numbering continues from phase 6, which ended at D38.

**D39. Structured shapes are new type tokens, not a new tuple
element.** A declaration stays five or six elements. `ARGTYPES` grows
`diskspec`, `arrayofdiskspec`, `networkspec`, `arrayofnetworkspec` and
`videospec`, each rendering a complete JSON Schema object fragment with
`properties`.

The alternative — a seventh element carrying a properties dict at each
declaration site — was rejected because it puts the same schema in two
places the moment a shape is used twice, and networkspec already is.
More importantly it breaks the module's stated design rule: the
compiler reads the *rendered specification*, so a shape that is not in
`ARGTYPES` is a shape the published OpenAPI and the enforced check can
disagree about. `swagger_helper` already deep-copies `ARGTYPES[token]`
for exactly this family of reasons (`base.py:688`), and the array
tokens already nest an `items` dict through that copy, so nothing about
the rendering path needs to change.

**D40. Shapes render inline, from one shared Python constant.** No
OpenAPI `definitions` section and no `$ref`. The five tokens are built
in `base.py` from three module-level constants, so networkspec's single
and array forms cannot drift, but each renders its properties inline
into the operation that declares it. `$ref` would be tidier in the
published document and would require teaching both the renderer and the
compiler to resolve references, for two operations that share a shape.

**D41. Unknown keys are refused.** The schemas publish
`additionalProperties: false` and compile to marshmallow schemas with
`unknown=RAISE`.

This is the decision most likely to be argued with, and it is the one
that makes the phase worth doing. A silently discarded `siz` key is the
original complaint of #936, and a validation layer that types the keys
it knows while ignoring the ones it does not has fixed the smaller half
of the problem. But it is also the only change here that can break a
caller who is doing something that works today, so it is conditional on
step 1: if the key census finds any caller sending an undocumented key
that the handler acts on, that key is documented and typed rather than
refused; if it finds one the handler ignores, the decision is re-argued
in writing before step 3 writes the schemas.

**D42. Every existing handler guard stays.** Not one of the checks in
`_netdesc_safety_checks`, the disk-bus check, the IDE refusal or the
videospec presence checks is deleted, even where a schema now subsumes
it.

This is phase 6's D34 in a new setting. `warn` and `off` are rollbacks:
an operator who turns validation off because this phase broke their
fleet must get the behaviour they had before it, not a newly
unguarded API. The schemas are strictly additive. The cost is
duplicated checking on the enforce path, which is cheap and which the
tests make visible rather than hiding.

**D43. An `enum` is published only where the server refuses outside
it.** Two of the enums this phase publishes are already enforced
(`disk[].bus`, by `_get_defaulted_disk_bus` plus `_get_disk_device`;
`network[].macaddress`'s pattern, by #4183). The rest —
`disk[].type`, `network[].model`, `video.model`, `video.vdi` — are
documented as enums and enforced nowhere, so publishing them *is* the
enforcement, and step 3 reads the value out of the code that consumes
it rather than out of the documentation that describes it. Phase 2's
`netblock` reasoning is the precedent in the other direction: a
published constraint narrower than the server is a documentation commit
compiled into a 400.

**D44. `network_uuid` is required inside the netdesc schema, typed
`string`, with no `uuid` format.** Required because
`_netdesc_safety_checks` already refuses a netdesc without it. `string`
because that closes F7's only reachable path. No `uuid` format, because
the parameter accepts a network *name* as well as a UUID — that is what
`from_db_by_ref` is for, and phase 6's rule that a validator must be no
narrower than the handler applies.

**D45. `size`-or-`base` stays a handler guard, and gains one.** The
schema types `size` as a non-negative integer and `base` as a string;
it cannot express "at least one of these two", and Swagger 2.0 has no
`anyOf`. Today neither is required and `disk: [{}]` is accepted, which
is a bug (F6). The handler gets an explicit guard for it in the same
step, next to the existing `instance must specify at least one disk`.

**D46. Integers compile strict.** `fields.Integer(strict=True)`
wherever the rendered type is `integer`, at every nesting depth. F8 is
the argument: under a check-only design, a field that accepts `1.5` as
an integer and hands the handler `1.5` has validated nothing and has
told the caller it did. `fields.Float` is left alone for the reason F8
records.

**D47. `metadata` and `bound_claims` get no element schema**, for the
reasons in F3 and F4, recorded in comments beside their declarations so
the next reader does not re-derive them.

**D48. A nested failure names its path.** `disk[0].size`, not `disk`.
The `arrayofstring` behaviour in F1 already produces `side_channels[0]`
and sets the expectation; marshmallow's nested errors arrive as a
nested dict and `_schema_findings` has to flatten them. A finding that
names only the top-level parameter would make a diskspec list of six
unusable to debug.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | high | opus | none | **The key and value census.** For each of diskspec, networkspec and videospec, enumerate every key any caller sends and every value type it sends, from five sources: the tree (`shakenfist/deploy/collection/plugins/modules/sf_instance.py`, which builds netdescs at lines 317-364), the client (`../client-python/shakenfist_client/`, note `apiclient.py:669` already coerces `size` to `int`), the CI suites (`shakenfist/deploy/shakenfist_ci/`), the API reference (`docs/developer_guide/api_reference/instances.md:67-130`, which documents four disk keys, five network keys and three video keys), and the handlers themselves (`external_api/instance.py` and `shakenfist/instance.py`, for every key read off a spec). Produce a table, one row per key, with: which sources send it, what types they send, what the handler does with each type, and whether it is documented. This is the input to D41 and D43 and the table goes in this plan. Do not change any code. |
| 2 | high | opus | none | **The compiler's object branch.** In `shakenfist/external_api/validation.py`, add a branch to `_field()` (around line 430, beside the existing `array` branch) for a rendered `{'type': 'object', 'properties': {...}}`: build a marshmallow schema from the properties with `Schema.from_dict`, honour `required` and `additionalProperties: false` (`unknown=RAISE`), and return `fields.Nested`. An object with no `properties` must keep compiling to `fields.Dict` exactly as now, because `metadata` and `bound_claims` rely on it (D47). Apply D46 in the same step: `fields.Integer(strict=True)` wherever the rendered type is `integer`. Then D48: make `_schema_findings` flatten marshmallow's nested error dict into dotted, indexed paths so a finding reads `disk[0].size`; `side_channels[0]` is the shape to match. Read the comments in that file first — they explain why every check is keyed on the rendered specification rather than on the type token, and that rule binds this change. |
| 3 | high | opus | none | **The three schemas.** In `shakenfist/external_api/base.py`, add `diskspec`, `arrayofdiskspec`, `networkspec`, `arrayofnetworkspec` and `videospec` to `ARGTYPES` (around line 380), built from three module-level constants so the single and array forms cannot drift (D40). Populate them from step 1's census, not from the documentation. Every `enum` must be read off the code that consumes the value: the libvirt XML templating in `shakenfist/instance.py` for `video.model` and `network[].model`, `instance._get_defaulted_disk_bus` and `_get_disk_device` for `disk[].bus`, and whatever consumes `video['vdi']` (start at `external_api/instance.py:1802` and `:1865`) for the VDI protocols. `network_uuid` per D44. `additionalProperties: false` per D41, unless step 1 found a reason to revisit it. Update every affected row of `STRUCTURED_PARAMETERS` in `shakenfist/tests/external_api/test_openapi_spec.py:104` in the same commit, with a comment per entry saying what backs the constraint — that table's own comment explains the standard. |
| 4 | medium | sonnet | none | **The declarations, and the guard D45 asks for.** Change the six declarations in F2's table to the new tokens: `external_api/instance.py:514` (`network` → `arrayofnetworkspec`), `:518` (`disk` → `arrayofdiskspec`), `:535` (`video` → `videospec`), `:1134` (`network` → `networkspec`). Leave `metadata` at `dict` and add the comment D47 asks for; do the same for `bound_claims` at `auth.py:1125` and `:1208`. **Delete no handler guard** (D42). Add the missing guard from D45 beside `instance must specify at least one disk` at `instance.py:673`: a diskspec with neither `size` nor `base` is a 400. |
| 5 | high | opus | none | **The nested sweep.** A new `shakenfist/tests/external_api/test_nested_sweep.py`, modelled on `test_required_sweep.py` and subclassing its `RequiredSweepTestCase` fixture, which already carries the whole decorator stack and working instance, network and blob fixtures. One row per (spec, key, sent value), with the status and whether an exception was recorded — pin every row of F5 and F6 above at its *new* answer, plus one accepted value per key so the schemas are shown not to be too narrow. Run it at `enforce`; add a second class at `warn` proving D42, that every row answers exactly what F5 and F6 measured before this phase. Mutation-test it: break each schema on purpose and confirm the right row fails with the right message, and keep the mutations in a script beside the test as the pr-re-review skill's adversarial pass asks. Cover the interface hotplug endpoint as well as instance create — it is the one with no scheduler in front of it, so its answers are 200s rather than 507s. |
| 6 | medium | sonnet | none | **Documentation.** `docs/developer_guide/api_reference/instances.md:67-130` gains, per spec, which keys are required, what each value's type and enum are, and that an unknown key is now refused. `docs/developer_guide/writing_an_endpoint.md` gains the structured tokens beside the rest of the vocabulary and says how to add another one. A release note in `docs/release_notes/v07-v08.md` says plainly that a request carrying an undocumented key inside a diskspec, networkspec or videospec now answers 400 where it used to be ignored, and names `API_VALIDATION_MODE=warn` as the rollback. |
| 7 | medium | sonnet | none | **Cluster CI.** Add cases to `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/` for the contract changes a unit test cannot prove are real: an unknown diskspec key answering 400, a non-string `network_uuid` answering 400 rather than 500, and a well-formed instance with every documented key still creating. Follow the cases phase 6 added in `cluster_ci_tests/test_networking.py`. |
| 8 | high | opus | none | **Close out.** Rewrite #528's body per F9. Audit the definition of done item by item, in this plan, the way phase 6's step 6 did. Record what landed in the master plan's `Merged` column *after* the merge, from the first-parent range — this is the mistake #4222 exists to correct. |

## Risks and mitigations

**An `enum` narrower than the server breaks working instances.** The
one that would hurt most is `network[].model`: the documentation lists
eight NIC models, and whether libvirt accepts a ninth is a question
about the XML template, not the docs. *Mitigation:* D43 makes step 3
read the consuming code, and step 1's census names every value any
caller actually sends. The reviewer's second job, stated at the top of
this plan, is to check each enum against the code.

**`additionalProperties: false` breaks a caller sending an
undocumented key.** *Mitigation:* step 1 censuses five sources before
step 3 writes anything, and D41 says in advance what happens if the
census finds one. The release note and `warn` are the operator's
rollback.

**A nested default is not applied, because the path is check-only.** A
`fields.Nested` schema with `load_default` values would look like it
fills in `bus` and `vdi`, and nothing would use them — the handler sees
the raw body. *Mitigation:* declare no defaults in the schemas, and
have step 5 assert that a handler receives exactly the body that was
sent. The existing default-filling in `instance.py:833` stays and is
the only thing that defaults anything.

**The error message degrades.** A marshmallow nested failure is a
nested dict, and a naive rendering produces something like
`disk: {0: {size: ['Not a valid integer.']}}` in a response body that
phase 4 spent a whole step making uniform. *Mitigation:* D48, and step
5 pins the exact strings.

**The autofixer lands one of these while the branch is open.** It has
happened to this plan twice, most recently colliding with #4199 inside
phase 6. *Mitigation:* #4223 was filed with `automated-fix-attempted`
already applied. Any issue filed during this phase gets the same at
filing time, not afterwards.

**Duplicated checking drifts.** D42 keeps the handler guards, so
`network_uuid` is now refused in two places with two messages.
*Mitigation:* the enforce-mode sweep pins which message a caller sees,
so a change to either that alters the answer fails a test.

## Definition of done

1. `_field()` in `shakenfist/external_api/validation.py` has a branch
   for `{'type': 'object', 'properties': ...}`, and an object *without*
   `properties` still compiles to `fields.Dict` — proven by a test that
   `POST /instances` still accepts a `metadata` value of a dict and of
   a list under the same key.
2. `grep -n "strict=True" shakenfist/external_api/validation.py` shows
   every `fields.Integer` construction, and `"cpus": 1.5` on
   `POST /instances` answers 400 rather than 500.
3. `ARGTYPES` carries `diskspec`, `arrayofdiskspec`, `networkspec`,
   `arrayofnetworkspec` and `videospec`, and the single and array forms
   of networkspec are the same Python object by construction — proven
   by a test asserting the rendered `items` equals the rendered single
   form, not by reading.
4. Every row of F5 answers 400 naming the offending key, and no row
   writes an exception record.
5. Every row of F6 answers 400, except any key step 1's census showed a
   caller sends, each of which has a recorded reason in this plan and a
   row in the sweep asserting it is still accepted.
6. A finding inside an array names its index: the response to a bad
   `size` in the second diskspec contains `disk[1].size`.
7. Under `API_VALIDATION_MODE=warn`, every row of F5 and F6 answers
   what this plan measured it answering before the phase — the
   rollback is real, and the sweep's warn class is the proof.
8. Every handler guard listed in D42 is still present:
   `_netdesc_safety_checks` still refuses a netdesc that is not a dict,
   one with no `network_uuid`, and a malformed MAC; the disk bus check
   and the IDE refusal are unchanged.
9. A diskspec with neither `size` nor `base` answers 400 (D45).
10. `STRUCTURED_PARAMETERS` agrees with the published specification —
    `test_every_published_structure_or_bound_is_registered` passes —
    and every entry this phase changed carries a comment saying what
    backs it.
11. No page states a spec's shape differently: the API reference, the
    schemas in `ARGTYPES`, and the release note all name the same keys,
    the same types and the same enums. A script in the test suite
    compares the reference's key lists against `ARGTYPES` rather than a
    reviewer comparing them by eye.
12. #528's body describes element schemas rather than `use_kwargs`
    coverage, and says the `use_kwargs` residual was overtaken by phase
    4.
13. #4167 is closed, or its remaining `uefi`/`secure_boot` scope is
    restated in the issue so that what is left is what is actually
    left.
14. The master plan's Execution table reads `Complete` for phase 7 with
    a `Merged` SHA taken from the first-parent range after the merge,
    and the `docs/plans/index.md` row reads `8 of 9`.
15. `pre-commit run --all-files` is clean.

## Back brief

Before executing any step, back brief the operator on the plan as
understood and how the intended work aligns with it.

Two gates, both cheap to ask about and expensive to redo:

* **After step 1, before step 3.** The census is what D41 and D43 are
  conditional on. If it finds a caller sending an undocumented key, or
  an enum value the code accepts and the documentation does not list,
  say so and get a decision before writing the schemas. Writing them
  first and narrowing later means rewriting step 5's sweep too.
* **After step 3, before step 4.** The rendered schemas are the
  contract. Show them — the actual `ARGTYPES` fragments — and the enum
  sources they were read from, before any declaration starts using
  them.

## Appendix: the survey probe

The probe was a single file, `test_zz_probe.py`, placed in
`shakenfist/tests/external_api/`, subclassing `RequiredSweepTestCase`
with `mode = 'enforce'` and overriding nothing but adding one test
method. It drove a list of `(label, body overlay)` pairs through
`self.client.post`, counted `self.mock_record_exception.call_count`
either side of each request to detect a recorded fault, and wrote its
results to a file rather than to stdout, because stestr captures
stdout.

It was run with
`.tox/py3/bin/python -m stestr run --no-subunit-trace 'test_zz_probe.ProbeTestCase.test_probe'`
— naming the single test method, because inheriting the fixture also
inherits the parent's tests, and running the phase 6 sweep at `enforce`
fails every row of it for reasons that have nothing to do with the
probe.

It was deleted rather than committed. Step 5 builds the committed
version, which differs in that it asserts rather than reports.
