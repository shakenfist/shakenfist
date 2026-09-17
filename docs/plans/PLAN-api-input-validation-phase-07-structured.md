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

## Amendments

**2026-09-17, issue #4242.** The review of #4232 also found the gap in
how D43 was implemented, and because the auto-filer did not run for
that approved PR, it was filed by hand as
[#4242](https://github.com/shakenfist/shakenfist/issues/4242). D43's
reasoning stands -- neither `model` carries an enum, and the working
vocabulary is still the hypervisor's -- but "no enum" had been built
as "no constraint at all", and both values are interpolated into
quoted attributes of the domain XML, rendered with escaping off. An
authenticated user could therefore close the attribute and write
device elements into their instance's domain definition. The fix is
the shape this phase's own guards taught: a published character-class
`pattern` (`api_base.DEVICE_MODEL_PATTERN`, wide enough for every
qemu model name) which enforces at `enforce`, and render-time
escaping (`instance._xml_attribute_escape()`, applied in
`_create_domain_xml()` and `hot_plug_interface()`) which holds at
`warn` and `off`, where a schema pattern rolls back. Three
`*.model.injection` sweep rows pin the mode split, and
`test_instance.InstanceDomainXMLEscapingTestCase` pins the escaping
with payloads that are well-formed injections without it. D43's table
below is unchanged: the pattern is not an enum and publishes no
vocabulary.

**2026-09-17, after the pull request review.** The automated review of
[#4232](https://github.com/shakenfist/shakenfist/pull/4232) raised one
`fix`, two `document` items and three `consider` items. All six were
taken. Four of them change what this plan says, and the changes are
made in place below rather than only recorded here:

* **A third handler guard, and D42's count moves from two to three.**
  The videospec's `model` and `memory` checks were *presence* tests, so
  an explicit null passed them and was stored on the instance —
  `self.video['vdi'].startswith('spice')` in a later console request,
  and `type='None'` rendered into the domain XML at `libvirt.tmpl:206`.
  That is exactly finding F6's videospec row, which this phase set out
  to close and did not. They are value tests now (`is None`, the same
  spelling `_netdesc_safety_checks` uses), and being handler guards
  they hold at `warn` and `off` like the other two. A non-mapping
  videospec needed an explicit shape guard in front of them, because
  the presence tests answered 400 for one by accident: `'model' not in
  ['vga']` is a membership test which happens to be True.
* **The census is wrong about `float`.** Its networkspec row said
  `"false"` does not float the interface. Python truthiness says
  otherwise — a non-empty string is true — so the schema published
  `"false"` as a boolean meaning False while the handler floated the
  interface. `validation.declared_boolean()` is now the one reading,
  keyed on marshmallow's own truthy and falsy sets so the check and the
  read cannot drift. The row is corrected.
* **A sixth spelling of the diskspec which asks for nothing.**
  `base` of the literal string `"none"` is read as no base by
  `util_general.noneish`, so `{"base": "none"}` alone is refused by
  D45's guard. True since step 4 and neither swept nor documented;
  both now.
* **The `_schema` sentinel collapse could not tell the sentinel from a
  caller's key of that name.** `_flatten_messages` now discriminates on
  whether the element is a mapping, which is the only state in which
  marshmallow's sentinel is reachable, so `{"disk": [{"_schema": 1}]}`
  answers `disk[0]._schema: Unknown field.` and a non-mapping element
  still answers `disk[0]: Not a valid mapping type.`

The two `document` items are the user guide's `-D` and `-N` key lists,
which did not say that an unlisted key is now refused, and the
collection's broken `video` parameter, which this plan said somebody
should file and nobody did. It is
[#4236](https://github.com/shakenfist/shakenfist/issues/4236). The
remaining `consider` item was the mutation harness, which now proves
the tree is green before it breaks anything — otherwise a tree which
was already red would report every mutation as caught.

**2026-09-16, after step 1.** The census this plan gated step 3 on
found three things the plan had wrong and one it had under-specified.
All four are corrected in place below; this note records what changed,
so a reader who remembers the original does not think the plan drifted.

* **D43 named six enums. Three of them are publishable.**
  `network[].model` and `video.model` go straight into the libvirt
  domain XML with nothing between (`libvirt.tmpl:139` and `:206`), so
  the real vocabulary is the hypervisor's qemu build. Worse, our own
  two documentation pages disagree about the first: `usage.md:288`
  recommends `ne2k_isa`, which `instances.md:105` omits. Publishing the
  API reference's list would answer 400 to a value our user guide tells
  people to use — phase 2's `netblock` reasoning, exactly.
* **D44's mechanism does not work.** Every compiled field is
  `allow_none=True` (`validation.py:382`) and the object branch
  recurses through the same `_field()`, so a `required`, `string`-typed
  nested `network_uuid` still accepts `null` and still reaches
  `from_db_by_ref(None, ns)`. The fix is not to break the nullability
  invariant: phase 6 already made an explicit `null` for a required
  parameter a 400 at the top level (`validation.py:838`), and the
  nested case applies that same rule one level down.
* **D46 asked for `fields.Integer(strict=True)`, which cannot be
  used.** `strict=True` refuses anything that is not already a Python
  `int`, and a query parameter is a string on the wire — `base.py`
  hands `flask.request.args.to_dict()` to `check()`, so `offset=10`
  arrives as `'10'`. Applying D46 literally broke
  `test_blob_data_bounds` within a minute. It is also narrower than the
  handler for bodies, since `{"cpus": "8"}` is accepted today by
  pydantic's lax mode. The decision is rewritten against the defect
  rather than against the Python type.
* **`video.memory` cannot be typed `integer` without a client
  release.** `docs/user_guide/consoles.md:95` documents
  `--videospec model=qxl,memory=65536,vdi=spiceconcurrent`, and the
  CLI's parser does `video[s[0]] = s[1]` with no coercion
  (`commandline/instance.py:499`), so that documented command puts the
  string `"65536"` on the wire. It works today only because jinja
  stringifies either type. Typing it is the operator's decision, taken
  deliberately; see D49 for the ordering it requires.

Two smaller corrections to finding F5's attributions, both from the
census reading the code rather than the traceback: the
`network[].address` 500 is raised by `util_general.noneish` at
`external_api/instance.py:369`, not by `n.ipam.is_in_range` below it;
and the `disk[].size` 500 is not pydantic, because
`InstanceData.disk_spec` is `list[dict[str, Any]]` with no validator.
F5's table is corrected. The second correction means `"size": "20"`
probably works end to end today, which makes D45's integer typing a
narrowing — see D50.

Nothing else the plan assumed was wrong. In particular D41 came
through unconditional, which is the outcome the census existed to test.

**2026-09-16, after step 5.** Rewriting D46 had a consequence nobody
followed through at the time, and four steps independently reported the
same thing. D46 originally asked for marshmallow's `strict=True`, which
refuses any value that is not already a Python `int`; its replacement
refuses a *fractional number* and accepts anything `int()` converts
faithfully, including a numeric string. Three of this plan's "this will
now be refused" claims were written against the original and are false
against what shipped:

* `disk[].size` as the string `"20"` is **accepted**, so D50's second
  narrowing never existed.
* `video.memory` as the string `"65536"` is **accepted**, so D49's
  ordering obligation never existed and the documented
  `--videospec memory=65536` keeps working.
* `network[].float` as `"yes"` is **accepted**, because marshmallow's
  `Boolean` takes it, which is correctly no narrower than the handler's
  bare truthiness test.

D49, D50 and definition-of-done items 5, 7 and 8 are corrected below.
The lesson is worth more than the corrections: a decision rewritten
mid-phase invalidates every *other* statement that was reasoning from
it, and nothing in this plan's structure made those statements findable.
Step 8's audit should re-read the decisions as a set rather than
item by item.

Two narrowings the plan never named were also found, both real and both
shipped. `additionalProperties: false` refuses a caller-supplied
`blob_uuid` inside a diskspec, which the handler genuinely acted on
(`instance.py:935`, `:974`); the documented spelling
`base: "sf://blob/<uuid>"` reaches the identical branch and is
unaffected. And D45's size-or-base guard is a handler guard, so it
holds at `warn` and `off` too — it is the one narrowing in this phase
an operator cannot roll back.


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
| `disk[].size` a non-numeric string or a dict | `int()` in `instance._safe_int_cast` (`instance.py:112`, from `:940`) — `ValueError` or `TypeError` |
| `network[].network_uuid` an int, a dict or a bool | `AttributeError: 'int' object has no attribute 'replace'` in `util/general.py:valid_uuid4` |
| `network[].address` an int or a list | `AttributeError` in `util_general.noneish`, from `external_api/instance.py:369` |
| `network[].model` an int | pydantic, building the `NetworkInterface` |

Items 2 and 3 of #4167 are the first and third rows and are exactly as
filed, eleven months on. The second, fourth and fifth rows are new —
#4167 found two by inspection and stopped, which is the difference
between reading and sending.

Two of the attributions in that table were corrected by step 1's
census, which read the code where the probe had only read the status.
The `address` row is `noneish` above `is_in_range`, not `is_in_range`;
and the `size` row is `_safe_int_cast`, not pydantic, because
`InstanceData.disk_spec` is `list[dict[str, Any]]` and validates
nothing. That second correction narrows the row: `"size": "20"` reaches
`int("20")` and works, so only a *non-numeric* string faults, and
typing `size` as an integer is therefore a narrowing rather than a
straight fix. D50 records it as one.

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

## The key and value census (step 1)

Five sources: the tree, the client repository, the CI suites, the
documentation and the handlers. The full working is in the step 1
report; what follows is the part later steps have to agree with.

## diskspec

Declared `arrayofdict` at `external_api/instance.py:518`.

| Key | Documented? | Sent by | Types sent | What the handler does with each type | Enum candidate? |
|-----|-------------|---------|------------|--------------------------------------|-----------------|
| `size` | yes, "integer, GB" (`instances.md:71`) | ansible `disks:`/`diskspecs:` (`sf_instance.py:390`, `:398`); CLI `-d` (`commandline/instance.py:452`) and `-D` (string, then int-coerced at `apiclient.py:669-670`); CI everywhere (`shakenfist_ci/base.py:1671` and ~150 sites) | `int`; `None` (ansible `diskspecs` default at `sf_instance.py:398`, and `apiclient.py:669`'s `and d['size']` guard deliberately leaves a falsy size uncoerced); **absent** (`test_ci_capacity_wait.py:242`'s sizeless cdrom, and `guest_ci_tests/test_boot.py:16` scenarios) | int → `_safe_int_cast` (`instance.py:940`) into `block_devices`, then `util_image.create_cow`/`create_blank`. `None`/absent → explicitly skipped by `scheduler.py:471-473`, `scheduler.py:577`, `mariadb.disk_spec_virtual_gb` (`mariadb.py:24703-24705`) and `util_image.create_cow` (`image.py:103`, `:115`), meaning "the size of the base image". A numeric string reaches `int()` in all three and works; `InstanceData.disk_spec` is `list[dict[str, Any]]` (`schema/instance_data.py:67`) and coerces nothing | no |
| `base` | yes, string (`instances.md:72`) | ansible (`sf_instance.py:388`, `:396`); CLI `-d`/`-D`; CI (~110 sites) | `str` (a `sf://upload/...`, `sf://blob/...`, `sf://snapshot/...`, `label:...`, bare name, or plain URL); `None` (ansible `sf_instance.py:388`, `:396`, CLI `commandline/instance.py:453`) | `None`/`''`/`'none'` → `util_general.noneish` (`util/general.py:121`) true → `d['disk_base'] = None`, blank disk. A string is prefix-dispatched at `external_api/instance.py:691-780`. A non-string truthy value is the `AttributeError` in `noneish` that F5 row 1 measured | no — free-form |
| `bus` | yes, enum (`instances.md:75`) | ansible (always, as `None`, `sf_instance.py:389`, `:397`); CLI `-d` (always `None`, `commandline/instance.py:454`) and `-D`; CI: `guest_ci_tests/test_disks.py:38` sends `'nvme'`, `cluster_ci_tests/test_disk_specs.py:51` sends `'banana'` and asserts a 400 | `str`; `None` | `None`/absent → `config.DISK_BUS`, default `'virtio'` (`instance.py:80-84`, `config.py:1026`). A value not in `_get_disk_device`'s table 400s at `external_api/instance.py:682-687`. `'ide'` additionally 400s at `:822-825` (unreachable — the bus check fires first) | **yes, already enforced** |
| `type` | yes, enum disk/cdrom (`instances.md:81`) | ansible (always `'disk'`, `sf_instance.py:391`, `:399`; `'cdrom'` via `ansible_module_ci/004.yml:115`); CLI `-d` (always `'disk'`); CI ~115 `'disk'`, one `'cdrom'` (`cluster_ci_tests/test_disk_specs.py`) | `str` | `_get_defaulted_disk_type` (`instance.py:105-109`) defaults to `'disk'` and passes the value through to `present_as`, which is rendered raw as `device='...'` in the domain XML (`libvirt.tmpl:50`). Only `'cdrom'` is special-cased, at `instance.py:1790` (raw image, no COW, `snapshot_ignores`) and `:1869` (virtio→usb bus swap). Anything else behaves as a disk in our code and is handed to libvirt | **yes, unenforced** |
| `blob_uuid` | no | nobody. Written *by* the handler (`external_api/instance.py:713`, `:739`, `:765`, `:768`) and read at `instance.py:935`, `:974`, `:1410` | n/a | server-populated | n/a |
| `disk_base` | no | nobody. Written by the handler at `external_api/instance.py:690` | n/a | server-populated | n/a |

### Undocumented keys that some caller sends

**None.** The two undocumented keys the handler reads (`blob_uuid`,
`disk_base`) are written by the handler itself and never arrive from a
caller. The ansible module is aware of them — `SERVER_POPULATED_DISK_KEYS`
(`sf_instance.py:211`) — but only to *strip them from the server's
response* before a dirtiness comparison (`sf_instance.py:427-433`); the
disks it sends are built fresh at `:384-413`. Nothing in the client or
the CI ever posts a disk spec it read back from a `GET`.

So `additionalProperties: false` on the diskspec breaks no first-party
caller. It will break a human who typed `-D siz=20`, which is the
intent.

### Documented keys nobody sends

## networkspec

Declared `arrayofdict` at `external_api/instance.py:514` (create) and
`dict` at `:1134` (interface hotplug). Both go through
`_netdesc_safety_checks` (`:330`) and the create path also through
`_netdesc_allocate_address` (`:387`).

| Key | Documented? | Sent by | Types sent | What the handler does with each type | Enum candidate? |
|-----|-------------|---------|------------|--------------------------------------|-----------------|
| `network_uuid` | yes, "uuid" (`instances.md:99`) | ansible `networks:` (`sf_instance.py:317`) and `networkspecs:`; CLI `-n`/`-f`/`-N` (`commandline/instance.py:472`, `:482`) and `add-interface` (`:997`, `:1008`); CI (~110 sites) | `str` — a UUID, or a **name**: `cluster_ci_tests/test_networking.py` sends `'barry_net'`, and `docs/user_guide/usage.md:255-262` documents naming a network | `Network.from_db_by_ref(value, namespace)` at `external_api/instance.py:358`. Presence is checked (`:334`), value is not — hence #4223. Normalised back to a UUID string at `:367`. A non-string is the `AttributeError` in `valid_uuid4` F5 row 3 measured | no — D44 says `string`, no `uuid` format |
| `macaddress` | yes, colon-separated, either case (`instances.md:100`) | CLI `-n`/`-f`/`add-interface` send it **as `None` on every call** (`commandline/instance.py:473`, `:483`, `:998`, `:1009`); CI sends real MACs (`guest_ci_tests/test_networking.py`, `smoke_ci_tests/test_agentops.py:300`) and one malformed one asserting a 400 (`guest_ci_tests/test_networking.py:135`) | `str`; **`None`** | `if netdesc.get('macaddress'):` (`:346`) — falsy skips the check; `NetworkInterface.new` (`network/interface.py:143-145`) then generates one. A truthy value must match `util_network.valid_macaddr` (`util/network.py:431-440`, `fullmatch` against `_MACADDR_BODY`), else 400 at `:348-351` | pattern, already enforced |
| `address` | yes, string (`instances.md:103`) | CLI `-n netuuid@addr` / `-f` / `-N address=...` (`commandline/instance.py:478`, `:487`, `:1003`); ansible `networkspecs:`; CI (`guest_ci_tests/test_cloudinit.py:85` sends **`None` explicitly**; `cluster_ci_tests/test_networking.py:181` sends addresses) | `str` (an IPv4 address, **or the literal `'none'`** — `docs/user_guide/usage.md:281-284`); `None` | `noneish` twice: `:369` skips the range check, `:402-403` turns it into `None` meaning "this interface has no address" (the comment at `:398-401` credits OpenStack Kolla). Falsy/absent → `reserve_random_free_address` (`:406`). Otherwise `n.ipam.is_in_range` (`:372`), then `n.ipam.reserve` (`:417`) | no |
| `model` | yes, enum of 8 (`instances.md:105`) / **9 in `usage.md:288`** | ansible `networks:` sends `'virtio'` (`sf_instance.py:318`); CLI `-n`/`-f` send `'virtio'` (`commandline/instance.py:474`, `:484`, `:999`, `:1010`); CI omits it | `str` | defaulted to `'virtio'` when absent or falsy (`:430-431`), stored on the `NetworkInterface` (`network/interface.py:165`), rendered raw as `<model type='{{net.model}}'/>` (`libvirt.tmpl:139`). **Nothing in `shakenfist/` restricts it** | **yes, unenforced — see below** |
| `float` | yes, boolean (`instances.md:109`) | ansible `networks:` sends `False` (`sf_instance.py:319`) and `networkspecs:` parses to a real bool (`sf_instance.py:329-333`); CLI `-f` sends `True` (`commandline/instance.py:475`), `-N float=...` parses `'true'`/`'True'` to a bool (`commandline/instance.py:325-329`); CI omits it | `bool` | `validation.declared_boolean(netdesc.get('float'))` (`:472`) — marshmallow's own truthy and falsy sets, so `"yes"` floats and `"false"` does not. **Corrected after review:** this was `if 'float' in netdesc and netdesc['float']`, a bare truthiness test on the raw body, for which `"false"` is a non-empty string and therefore floats. The schema published the value as a boolean meaning False while the server inverted it | no |
| `iface_uuid` | no | nobody. Written by the handler at `external_api/instance.py:455`; read at `:1193`, `:1205` and `operations/node_inst_netdesc_op.py:391`, `:464` | n/a | server-populated | n/a |

### Undocumented keys that some caller sends

**None.** `iface_uuid` is written by `_netdesc_allocate_address` after
every caller check has run, and it never appears in a response body a
caller could echo (`Instance.external_view` carries `interfaces`, not
netdescs — `instance.py:643-660`). `order` appears alongside netdesc
keys only in a unit-test fixture
(`tests/schema/operations/test_node_inst_netdesc_op.py:140`); the create
handler passes order as a positional argument (`:901`), not as a key.

`additionalProperties: false` on the networkspec breaks no first-party
caller.

### Documented keys nobody sends

None — all five are sent.

## videospec

Declared `dict` at `external_api/instance.py:535`. The handler's whole
check is `external_api/instance.py:833-841`: default the entire spec if
absent, else require the *presence* of `model` and `memory` and default
`vdi` to `'spice'`.

| Key | Documented? | Sent by | Types sent | What the handler does with each type | Enum candidate? |
|-----|-------------|---------|------------|--------------------------------------|-----------------|
| `model` | yes, enum vga/cirrus/qxl (`instances.md:119`) | CLI default `'cirrus'` (`commandline/instance.py:491`) and `--videospec model=...` (**string**, `:499`); CI `cluster_ci_tests/test_vdi_console_file.py:133` sends `'cirrus'` | `str` | presence required (`:837-838`); value rendered raw into `<model type='...'>` (`instance.py:2153` → `libvirt.tmpl:206`). Nothing checks it | **yes, unenforced** |
| `memory` | yes, "integer, KiB" (`instances.md:121`) | CLI default `16384` (**int**, `commandline/instance.py:491`); CLI `--videospec memory=65536` (**string**, `:499` — the parser never coerces); CI sends `16384` | `int` **and `str`** | presence required (`:839-840`); rendered raw into `vram='{{video_memory}}'` (`instance.py:2154` → `libvirt.tmpl:206`), where jinja stringifies either type identically. Nothing coerces or validates | no, but see below |
| `vdi` | yes, enum of 4 (`instances.md:123`) | CLI `--videospec vdi=...` (string); CI `cluster_ci_tests/test_vdi_console_file.py:133` sends `'spiceconcurrent'` | `str` | defaulted to `'spice'` if absent (`:840-841`); consumed in the five places listed above | **yes, unenforced; docs are correct** |

### Undocumented keys that some caller sends

**None.** The handler reads only `model`, `memory` and `vdi`; the whole
dict is stored verbatim on the instance (`instance.py:614`,
`schema/instance_data.py:95`, `video: dict[str, Any]`) and echoed in
`external_view` (`instance.py:655`), so an unknown key is stored and
returned but acted on nowhere.

### Documented keys nobody sends

None — all three are sent. Note that `apiclient.create_instance`'s
`video` kwarg defaults to `None` (`apiclient.py:625`) and the body
always carries `'video': video` (`apiclient.py:651`), so a raw
`apiclient` caller who does not pass one sends `"video": null` and the
handler's `if not video:` (`:833`) supplies the whole default. **The
videospec schema must tolerate a null for the parameter itself**, which
it does — `allow_none=True` at `validation.py:426`.

## Cross-cutting findings

### N1. Five documented keys are routinely sent as JSON `null`

`disk[].base`, `disk[].bus`, `disk[].type`, `disk[].size`,
`network[].macaddress`, `network[].address`. Every one of them is sent
as `null` by the shipped CLI, the shipped collection, or the CI suite on
a normal, working request, and every one has a handler branch that
treats `null` as "not supplied".

This is *already safe* by construction: `_field()` sets
`allow_none=True` on every compiled field (`validation.py:426`) and the
object branch recurses through `_field()`
(`validation.py:504-506`), so nested fields inherit it. Step 3 needs to
write no `x-nullable`; it needs only to **not** invent a nullability
check of its own.

It does however mean the *published* document will say
`{"type": "string"}` about a property whose dominant value is `null`.
That is the same compromise phase 3 already made at the top level (the
module docstring at `validation.py:141-150` records it, citing
`"source_url": null` and `"nvram_template": null`), so consistency
argues for leaving it alone and saying so in a comment.

### N2. F5's attribution for `network[].address` looks wrong

The plan's F5 row 4 puts the 500 for a non-string `address` "inside
`n.ipam.is_in_range`". Reading `external_api/instance.py:369`, the
guard is
`if netdesc.get('address') and not util_general.noneish(netdesc.get('address'))`
— for `address: 5` or `address: [ ... ]`, `noneish` is reached first and
raises `AttributeError: 'int' object has no attribute 'lower'`
(`util/general.py:124`) before `is_in_range` is called. Same status,
same recorded exception, different frame. Worth correcting in the plan
so step 5's sweep asserts against the right thing if it ever pins a
traceback.

### N3. F5's attribution for `disk[].size` may also be wrong

F5 row 2 says a string or dict `size` produces "pydantic
`ValidationError` from `InstanceData` inside `Instance.new`".
`InstanceData.disk_spec` is `list[dict[str, Any]]`
(`schema/instance_data.py:67`) with no validator
(`grep -n "validator" schema/instance_data.py` finds none), so it
accepts both. The likelier frame is `int(disk['size'])` at
`scheduler.py:473`/`:578`, which raises `ValueError` for `'banana'` and
`TypeError` for a dict — and note that `int('20')` *succeeds*, so
**`"size": "20"` very probably works end to end today**. Step 5 should
re-measure a numeric-string `size` specifically before D45's `integer`
typing refuses it; if it does work, the narrowing is still fine (no
first-party caller sends one, because `apiclient.py:669-670` coerces)
but it belongs in the release note.

### N4. The `sf_instance` ansible module sends `video` as a *string*

`sf_instance.py`'s `video` parameter is `{'type': 'str'}`
(argument_spec) and is passed straight through as a kwarg
(`sf_instance.py:456-462`), which reaches `apiclient.py:651` as
`'video': '<some string>'`. The parameter is declared `dict`, so
`fields.Dict()` already refuses it at `enforce` — this is broken *today*,
before phase 7, and phase 7 does not make it worse. Out of scope, but
somebody should file it: the collection's `video` parameter cannot work.
No in-tree playbook uses it (`ansible_module_ci/004.yml` does not), which
is why nobody has noticed.

**Filed after review as
[#4236](https://github.com/shakenfist/shakenfist/issues/4236),** carrying
`automated-fix-attempted` from the moment it was filed. The phase's
close-out audit did not catch that this paragraph had committed to
filing it and that nothing had; the review did.

### N5. `config.DISK_BUS`'s description is wrong

`config.py:1028-1031` says "One of virtio, scsi, usb, ide, etc. See
libvirt docs for full list of options". `ide` 400s every create
(`instance.py:99-100` → `external_api/instance.py:686`) and there is no
"full list" — the supported set is the five keys of `bases`
(`instance.py:92-98`). A one-line docstring fix; not this phase's, but
it is the same defect class D43 exists to prevent, one layer down.

---

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
it. Three enums, not six** — corrected by the census, which is what it
was for.

| Key | What the code accepts | Published? |
|-----|-----------------------|------------|
| `disk[].bus` | `sata, scsi, usb, virtio, nvme`, the `bases` dict at `instance.py:92` | **yes** — already enforced, docs agree |
| `video.vdi` | `vnc, spice, spiceconcurrent, spicedebug` (`instance.py:1715`, `:2161`, `libvirt.tmpl:156`) | **yes** — the one case where publishing genuinely is the enforcement |
| `disk[].type` | any string; only `cdrom` is special-cased (`instance.py:1790`, `:1869`) | **yes, as a narrowing** — see D50 |
| `network[].model` | any string, rendered raw at `libvirt.tmpl:139` | **no** |
| `video.model` | any string, rendered raw at `libvirt.tmpl:206` | **no** |

The two `model` keys were the plan's own named risk and the census says
the risk is real. Both are rendered into the domain XML with nothing
between, so the server's true vocabulary is the hypervisor's qemu
build, which varies by node and by release and which this API cannot
know. `usage.md:288` documents `ne2k_isa` and `instances.md:105` does
not list it; two documentation pages disagreeing is itself proof that
neither is a specification. Both are typed `string` with a description
naming the common values and no `enum`. If enforcement is ever wanted
it belongs in a hypervisor capability check, not in a request schema.

`network[].macaddress` keeps the pattern #4183 already enforces, which
is not an enum and is unchanged by this phase.

**D44. `network_uuid` is required inside the netdesc schema, typed
`string`, with no `uuid` format — and `required` there means present
*and not null*.** Required because `_netdesc_safety_checks` already
refuses a netdesc without it. `string` because a non-string is finding
F5's third row. No `uuid` format, because the parameter accepts a
network *name* as well as a UUID (`usage.md:255` documents naming one,
and `cluster_ci_tests/test_networking.py` sends `'barry_net'`), and
phase 6's rule that a validator must be no narrower than the handler
applies.

The census corrected this decision's mechanism. Typing the property was
not going to close F7's path: every compiled field is `allow_none=True`
(`validation.py:382`) and the object branch recurses through the same
`_field()`, so `{"network_uuid": null}` would satisfy a required string
and still reach `from_db_by_ref(None, ns)`.

The answer is not `allow_none=False`, which would break a
module-wide invariant that exists for a good reason — an optional
declared parameter legitimately arrives as `null`, and the shipped
client sends five of them on every instance create (census finding N1).
It is that **`required` inside an object fragment carries the same
meaning phase 6 gave it at the top level**: present, and not an
explicit `null`. `validate_request` already applies exactly that rule
to `compiled.required_names` at `validation.py:838`, with a comment
explaining that a caller who sent `{"key": null}` should get the same
answer as one who sent no key at all, because it is one fact from the
handler's side. Applying it one level down is that rule, not a new one.

This closes the netdesc path. It does **not** close
[#4223](https://github.com/shakenfist/shakenfist/issues/4223), which
stays open and keeps its own fix: the lookup function is still wrong
for the next caller who reaches it by another route.

**D45. `size`-or-`base` stays a handler guard, and gains one.** The
schema types `size` as a non-negative integer and `base` as a string;
it cannot express "at least one of these two", and Swagger 2.0 has no
`anyOf`. Today neither is required and `disk: [{}]` is accepted, which
is a bug (F6). The handler gets an explicit guard for it in the same
step, next to the existing `instance must specify at least one disk`.

The census confirmed `minimum: 0` rather than `minimum: 1`, from four
code paths and an in-tree test. A sizeless or `None`-sized disk means
"the size of the base image" — `scheduler.py:471`, `scheduler.py:577`,
`mariadb.disk_spec_virtual_gb` (`mariadb.py:24703`) and
`util_image.create_cow` (`image.py:103`) all skip it explicitly, and
`test_ci_capacity_wait.py:242` relies on a sizeless cdrom. `size: 0` is
behaviourally identical to the documented `None`, so `minimum: 1` would
refuse something the documentation already shows working. A *negative*
size genuinely corrupts the capacity ledger (`scheduler.py:473`,
`mariadb.py:24710`), which is what the bound is for.

`size` must also stay nullable: the ansible module sends
`'size': None` on its `diskspecs:` path (`sf_instance.py:398`) and
`apiclient.py:669`'s `and d['size']` guard deliberately leaves a falsy
size uncoerced.

**D46. An integer field refuses a fractional number** — at every
nesting depth, bound to the `_SCALARS` mapping so a future branch of
`_field()` cannot forget it. F8 is the argument: under a check-only
design, a field that accepts `1.5` as an integer and hands the handler
`1.5` has validated nothing and has told the caller it did.

**This is deliberately not marshmallow's `strict=True`, which this
decision originally asked for and which cannot be used.** `strict=True`
refuses anything that is not already a Python `int`, and two kinds of
caller legitimately send something else:

* **A query parameter is a string on the wire.** `base.py:1907` hands
  `flask.request.args.to_dict()` to `check()`, so `offset=10` arrives
  as `'10'`. Every integer query parameter in the tree would answer
  `400 ... Not a valid integer.` on a request the server has always
  served. This is not a hypothetical: applying the decision literally
  failed `test_blob_data_bounds` on the first run.
* **A body integer sent as a JSON string reaches a handler that
  coerces it.** `{"cpus": "8"}` works today because pydantic's lax mode
  converts it, so refusing it would be a validator narrower than its
  handler — the breaking change dressed as a correctness fix that phase
  6's width rule exists to stop.

So the check is written against the defect rather than against the
Python type: **a finite float with a fractional part is refused, and
everything `int()` converts faithfully is left alone.** An integral
float such as `8.0` is accepted for exactly the reason `fields.Float`
is left alone — JSON has one numeric type, so `8.0` and `8` are the
same JSON number and reading one as the integer 8 invents nothing.
Infinity falls through to the base class, whose "Number too large."
message is better than anything this would write.

`fields.Float` is unchanged, for the reason F8 records.

**D47. `metadata` and `bound_claims` get no element schema**, for the
reasons in F3 and F4, recorded in comments beside their declarations so
the next reader does not re-derive them.

**D48. A nested failure names its path.** `disk[0].size`, not `disk`.
The `arrayofstring` behaviour in F1 already produces `side_channels[0]`
and sets the expectation; marshmallow's nested errors arrive as a
nested dict and `_schema_findings` has to flatten them. A finding that
names only the top-level parameter would make a diskspec list of six
unusable to debug.

Step 3b found the limit of this, and it is worth recording rather than
fixing. "One fact, one message" now holds *within* a nesting level but
not across them: an omitted required parameter at the top level answers
reason `missing-required` with `declared required but not supplied`,
while an omitted or null required *property* answers reason
`type-mismatch` with `Missing data for required field.` Rewording the
nested detail to match would have a `type-mismatch` finding speaking in
a reason code it is not counted under, which trades a cosmetic
inconsistency for a telemetry one. Left alone deliberately.

Step 4 found a regression this decision's machinery caused, now fixed:
wrapping a spec in `fields.Nested` made a non-mapping element answer
`network[0]._schema: Invalid input type.`, leaking marshmallow's
internal sentinel into a caller-facing message where F1 had already
blessed `network[0]: Not a valid mapping type.` as good. The flattener
collapses the schema-level key onto the container's own name, and the
two pre-existing tests asserting that string pass unchanged — which is
the evidence the case did not move, rather than a new assertion saying
it did not.

**D49. `video.memory` is typed `integer`. There is no ordering
obligation** — the original text of this decision said there was, and
it was wrong; see the second amendment. Corrected text follows.
The census found that the documented invocation in
`docs/user_guide/consoles.md:95` —
`--videospec model=qxl,memory=65536,vdi=spiceconcurrent` — puts the
*string* `"65536"` on the wire, because the CLI's parser does
`video[s[0]] = s[1]` with no coercion (`commandline/instance.py:499`)
and `apiclient.py` coerces a diskspec `size` but not a videospec
`memory`. It works today only because jinja stringifies either type on
the way into the domain XML.

Typing it was taken as the operator's decision on the understanding
that it broke the shipped CLI until a client release. **It does not.**
D46's rewrite means an integer field accepts a numeric string, so
`{"memory": "65536"}` is accepted and the documented invocation keeps
working — measured, and pinned as the sweep row
`video.memory.numeric_string`.

[client-python#398](https://github.com/shakenfist/client-python/issues/398)
is therefore a tidy-up rather than a release gate, and the release note
must not name it as a version requirement. It is still worth having:
validation is check-only, so the handler stores whatever the caller
sent, and a videospec whose memory is a string is a string in the
database. The coercion belongs in `apiclient.py` beside the disk-size
block it mirrors, so that every caller of the library is fixed rather
than only those who came through the command line.

**D50. Three narrowings are taken deliberately, and measured.** The
original text of this decision named two, one of which does not exist;
see the second amendment. The three that shipped are:

1. **`disk[].type` publishes `[disk, cdrom]`**, refusing libvirt's
   `floppy` and `lun`. Only `cdrom` is special-cased
   (`instance.py:1790` and `:1869`); every other value is treated as a
   plain disk by every code path we own and handed to libvirt as a
   device name, so what this refuses is input that produced a silently
   wrong result rather than a working one.
2. **`blob_uuid` inside a diskspec is refused**, by
   `additionalProperties: false`, because it is not a documented key.
   This one the census did not name and it is real: the handler acts on
   a caller-supplied `blob_uuid` (`instance.py:935`, `:974`). The
   documented spelling `base: "sf://blob/<uuid>"` reaches the identical
   branch — `external_api/instance.py:793` sets the key from it — so
   nothing a caller is told to do stops working.
3. **A diskspec with neither `size` nor `base` is refused**, by D45's
   handler guard, including a `size` of `0` with no `base`. Because it
   is a handler guard rather than a schema check it holds at `warn` and
   `off` too, which makes it the only narrowing in this phase an
   operator cannot roll back. That is deliberate — a disk that asks for
   nothing is not a rollback-worthy behaviour — but it should be said
   out loud.

What is **not** narrowed, despite the original text of this decision:
`disk[].size` as the string `"20"` is accepted, because D46 refuses a
fractional number rather than a non-`int`. Step 5 measured each of
these before and after rather than asserting any of it.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | high | opus | none | **The key and value census.** For each of diskspec, networkspec and videospec, enumerate every key any caller sends and every value type it sends, from five sources: the tree (`shakenfist/deploy/collection/plugins/modules/sf_instance.py`, which builds netdescs at lines 317-364), the client (`../client-python/shakenfist_client/`, note `apiclient.py:669` already coerces `size` to `int`), the CI suites (`shakenfist/deploy/shakenfist_ci/`), the API reference (`docs/developer_guide/api_reference/instances.md:67-130`, which documents four disk keys, five network keys and three video keys), and the handlers themselves (`external_api/instance.py` and `shakenfist/instance.py`, for every key read off a spec). Produce a table, one row per key, with: which sources send it, what types they send, what the handler does with each type, and whether it is documented. This is the input to D41 and D43 and the table goes in this plan. Do not change any code. |
| 2 | high | opus | none | **The compiler's object branch.** In `shakenfist/external_api/validation.py`, add a branch to `_field()` (around line 430, beside the existing `array` branch) for a rendered `{'type': 'object', 'properties': {...}}`: build a marshmallow schema from the properties with `Schema.from_dict`, honour `required` and `additionalProperties: false` (`unknown=RAISE`), and return `fields.Nested`. An object with no `properties` must keep compiling to `fields.Dict` exactly as now, because `metadata` and `bound_claims` rely on it (D47). Apply D46 in the same step: `fields.Integer(strict=True)` wherever the rendered type is `integer`. Then D48: make `_schema_findings` flatten marshmallow's nested error dict into dotted, indexed paths so a finding reads `disk[0].size`; `side_channels[0]` is the shape to match. Read the comments in that file first — they explain why every check is keyed on the rendered specification rather than on the type token, and that rule binds this change. |
| 3 | high | opus | none | **The three schemas.** In `shakenfist/external_api/base.py`, add `diskspec`, `arrayofdiskspec`, `networkspec`, `arrayofnetworkspec` and `videospec` to `ARGTYPES` (around line 380), built from three module-level constants so the single and array forms cannot drift (D40). Populate them from step 1's census, not from the documentation. Every `enum` must be read off the code that consumes the value: the libvirt XML templating in `shakenfist/instance.py` for `video.model` and `network[].model`, `instance._get_defaulted_disk_bus` and `_get_disk_device` for `disk[].bus`, and whatever consumes `video['vdi']` (start at `external_api/instance.py:1802` and `:1865`) for the VDI protocols. `network_uuid` per D44. `additionalProperties: false` per D41, unless step 1 found a reason to revisit it. Update every affected row of `STRUCTURED_PARAMETERS` in `shakenfist/tests/external_api/test_openapi_spec.py:104` in the same commit, with a comment per entry saying what backs the constraint — that table's own comment explains the standard. |
| 4 | medium | sonnet | none | **The declarations, and the guard D45 asks for.** Change the six declarations in F2's table to the new tokens: `external_api/instance.py:514` (`network` → `arrayofnetworkspec`), `:518` (`disk` → `arrayofdiskspec`), `:535` (`video` → `videospec`), `:1134` (`network` → `networkspec`). Leave `metadata` at `dict` and add the comment D47 asks for; do the same for `bound_claims` at `auth.py:1125` and `:1208`. **Delete no handler guard** (D42). Add the missing guard from D45 beside `instance must specify at least one disk` at `instance.py:673`: a diskspec with neither `size` nor `base` is a 400. |
| 5 | high | opus | none | **The nested sweep.** A new `shakenfist/tests/external_api/test_nested_sweep.py`, modelled on `test_required_sweep.py` and subclassing its `RequiredSweepTestCase` fixture, which already carries the whole decorator stack and working instance, network and blob fixtures. One row per (spec, key, sent value), with the status and whether an exception was recorded — pin every row of F5 and F6 above at its *new* answer, plus one accepted value per key so the schemas are shown not to be too narrow. Run it at `enforce`; add a second class at `warn` proving D42, that every row answers exactly what F5 and F6 measured before this phase. Mutation-test it: break each schema on purpose and confirm the right row fails with the right message, and keep the mutations in a script beside the test as the pr-re-review skill's adversarial pass asks. Cover the interface hotplug endpoint as well as instance create — it is the one with no scheduler in front of it, so its answers are 200s rather than 507s. |
| 6 | medium | sonnet | none | **Documentation.** `docs/developer_guide/api_reference/instances.md:67-130` gains, per spec, which keys are required, what each value's type and enum are, and that an unknown key is now refused. `docs/developer_guide/writing_an_endpoint.md` gains the structured tokens beside the rest of the vocabulary and says how to add another one. A release note in `docs/release_notes/v07-v08.md` says plainly that a request carrying an undocumented key inside a diskspec, networkspec or videospec now answers 400 where it used to be ignored, names the two narrowings of D50, names the client version D49 requires, and names `API_VALIDATION_MODE=warn` as the rollback. **Also fix the three documentation bugs the census found, none of which this phase caused:** `docs/user_guide/usage.md:237` lists `ide` as a valid disk bus and `config.DISK_BUS`'s description at `shakenfist/config.py:1028` does the same, while `external_api/instance.py:825` answers `400 IDE disks are no longer supported`; and `usage.md:288` lists nine NIC models where `instances.md:105` lists eight, the extra being `ne2k_isa`. Per D43 neither list is a specification, so say that the set is the hypervisor's rather than reconciling two prose lists into a third. |
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
2. Every compiled integer field, at every nesting depth, refuses a
   number with a fractional part and accepts an integral one and a
   numeric string — pinned by a registry-wide test that enumerates the
   compiled endpoints rather than by a grep. `"cpus": 1.5` on
   `POST /instances` answers 400 rather than 500, and `"cpus": "8"`
   still reaches the handler. (This item originally read `grep -n
   "strict=True" ...`, which D46's rewrite makes meaningless: the check
   is a field subclass bound into `_SCALARS`, not a keyword at a
   construction site, precisely so that nesting cannot forget it.)
3. `ARGTYPES` carries `diskspec`, `arrayofdiskspec`, `networkspec`,
   `arrayofnetworkspec` and `videospec`, and the single and array forms
   of networkspec are the same Python object by construction — proven
   by a test asserting the rendered `items` equals the rendered single
   form, not by reading.
4. Every row of F5 answers 400 naming the offending key, and no row
   writes an exception record.
5. Every row of F6 answers 400 **except these five values**, each of
   which is still accepted and has a row in the sweep asserting so:
   `network[].model` and `video.model` as any string (D43 — neither
   carries an enum, because both are rendered raw into the libvirt
   domain XML and the vocabulary is therefore the hypervisor's),
   `network[].float` as `"yes"` (marshmallow's truthy set is no
   narrower than the handler's bare truthiness test), and
   `disk[].size` and `video.memory` as numeric strings (D46 refuses a
   fractional number, not a string).

   (Twice corrected. This item originally excepted "any key the census
   showed a caller sends", which is vacuous — the census showed a
   caller sends every documented key; the exception has to be keyed on
   values, and named. It then named *four* values and omitted
   `video.model`, which step 8's audit caught: F6's last row is
   "`video.model` / `.memory` / `.vdi`, any type at all", and D43's
   own table says in as many words that `video.model` publishes no
   enum for exactly the reason `network[].model` does not. The
   deliverable was right throughout — `video.model.nonsense` has been
   an accepted row of the sweep since step 5, and the release note
   names both `model` keys — so this was the item's wording reasoning
   from half of D43, the same failure mode as the D46 rewrite's three
   dependents.)
6. A finding inside an array names its index: the response to a bad
   `size` in the second diskspec contains `disk[1].size`.
7. Under `API_VALIDATION_MODE=warn`, every row of F5 and F6 answers
   what this plan measured it answering before the phase, **except the
   ten rows that are handler guards rather than schema checks**: the
   null `network_uuid` on both routes, the diskspec that asks for
   nothing in its several spellings, and the videospec whose `model` or
   `memory` is an explicit null. Those answer 400 at every mode, by
   design — D42 says a guard keeps working under the rollback, and
   three of these guards are new in this phase precisely so that the
   rollback does not hand back the bug the phase closed. `off` is measured
   identically to `warn`, on the whole table rather than on a sample.
8. Every handler guard listed in D42 is still present:
   `_netdesc_safety_checks` still refuses a netdesc that is not a dict,
   one with no `network_uuid` — and now one whose `network_uuid` is
   null — and a malformed MAC; the disk bus check is unchanged. The
   IDE refusal at `external_api/instance.py:825` is unreachable at
   every mode, because the bus check answers `invalid disk bus ide`
   first; that was already true before this phase and is recorded here
   so the item does not read as though it were observable.
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

### Definition-of-done audit (step 8)

Every verdict below was produced by running something or by reading the
tree as it stands, never by trusting what a step reported. Where the
evidence is a measurement, the measurement is quoted.

Read the decisions as a set first, which is this phase's own recorded
lesson (see the second amendment). D46's rewrite has three dependents
in the plan — D49, D50 and definition-of-done items 5, 7 and 8 — all of
which were corrected after step 5. Two more dependents outside the plan
were still stale when this step started and are fixed in it: the
`video.memory` comment in `base.py` still said the typing was "a
narrowing with an ordering obligation attached", which is the sentence
D49's correction deletes, and
`test_a_video_memory_string_is_still_accepted`'s docstring in
`test_validation_compiler.py` still spoke of D49 in the present tense as
saying the typing "breaks the shipped CLI until client-python#398
ships". Neither changed any behaviour; both would have told the next
reader something the plan had already retracted. A third dependency was
found in this audit and is item 5 below.

1. **Met.** The object branch is `validation.py:579`, and it is keyed on
   `isinstance(spec.get('properties'), dict)` rather than on the type
   token — the comment at `:585` says so explicitly, because
   marshmallow's default for a `Schema` is `unknown=RAISE` and an
   object branch keyed on `type` would have refused every metadata key
   ever sent. `ARGTYPES['dict']` still renders a bare
   `{'type': 'object'}` and the compiled field is still `fields.Dict`
   (`test_validation_compiler.py:355`). The end-to-end proof the item
   asks for is
   `test_metadata_takes_a_dict_and_a_list_under_the_same_key`
   (`test_request_validation.py:1423`), which drives a real
   `POST /instances` carrying `{"k3s": {"a": "dict"}}` and then
   `{"k3s": ["a", "list"]}` and gets a 507 with no exception recorded
   both times.
2. **Met.** Measured over a real registry build rather than read: 27
   compiled integer fields, of which `disk[].size` and `video.memory`
   are nested (`_ExactInteger` both), and every one of the 27 refuses
   `1.5`, accepts `8.0` and accepts `'8'`. The registry-wide test is
   `test_every_compiled_integer_refuses_a_fractional_number`, which
   reaches nested fields through `_every_compiled_field`'s two ways
   down (a `List.inner` and a `Nested.schema.fields`) rather than
   through a grep, exactly as the item's parenthesis requires. End to
   end, `"cpus": 1.5` answers `400 cpus: Not a valid integer.` with no
   exception recorded, and `"cpus": "2"` reaches placement
   (`test_a_fractional_cpu_count_is_a_bad_request` and
   `test_a_numeric_string_cpu_count_still_reaches_placement`). One stale
   docstring was fixed in this step: `_every_compiled_field`'s said
   "the nested arm reaches nothing today, since no declaration carries
   a `properties` block until step 4", which stopped being true when
   step 4 landed and made the test read as weaker than it is.
3. **Met.** All five tokens are in `ARGTYPES`. The identity is real, not
   merely equal: `ARGTYPES['arrayofnetworkspec']['items'] is
   ARGTYPES['networkspec']` evaluates `True`, and so does the diskspec
   pair. `test_the_single_and_array_forms_cannot_drift` asserts both
   that identity and that the two *render* identically through the deep
   copy `swagger_helper()` makes, which is the assertion the item asks
   for since rendering is where a copy could diverge.
4. **Met.** All eleven F5 values, at `enforce`: 400, `recorded=False`,
   and an error naming the key and its index —
   `disk[0].base: Not a valid string.` (int, bool, list),
   `disk[0].size: Not a valid integer.` (non-numeric string, dict),
   `network[0].network_uuid: Not a valid string.` (int, dict, bool),
   `network[0].address: Not a valid string.` (int, list) and
   `network[0].model: Not a valid string.` (int). Each is a row of
   `CASES` in `test_nested_sweep.py` and each is asserted at all three
   modes.
5. **Met, as amended — and the amendment is the audit's main finding.**
   The item excepted four values and there are five: `video.model` as
   any string is an F6 value which is still accepted, and the item did
   not name it. The *deliverable* is right and always was —
   `video.model.nonsense` has been an accepted row of the sweep since
   step 5, `base.py`'s videospec comment says the vocabulary is the
   hypervisor's, D43's own table says `video.model` publishes no enum
   for exactly the reason `network[].model` does not, and the release
   note names both `model` keys in its "several things which look like
   narrowings are not" paragraph. What was wrong was this item's
   wording, which had reasoned from half of D43. That is the same
   failure mode the second amendment records for D46, arriving a third
   time, and it is the reason the amendment told this step to read the
   decisions as a set. The item is corrected above. With the fifth
   exception named, every other F6 value answers 400 at `enforce`:
   `size` of `-5`, `8.5` and `true`; a diskspec of `{}`; `type` of
   `"nonsense"` and of `5`; an unknown diskspec key; an unknown netdesc
   key; `video.vdi` of `"nonsense"`; and `video.memory` of `"lots"`.
6. **Met.** `disk.second_element` sends
   `{"disk": [{"size": 8}, {"size": "banana"}]}` and the sweep asserts
   the answer is `400 disk[1].size: Not a valid integer.` — the index,
   and the key, and not the container.
7. **Met.** The count of ten was checked against the table rather
   than against the number in the item. Exactly ten rows of `CASES`
   carry the phrase `moves at warn`, and they are the ten the item
   describes: `net.uuid.null` and `hotplug.uuid.null` (the null
   `network_uuid` on both routes); `disk.empty`,
   `disk.size_null_only`, `disk.size_zero_only`,
   `disk.size_and_base_null`, `disk.base_none_only` and
   `disk.unknown_key_only` (the diskspec which asks for nothing, in its
   six spellings — the last two are the typo'd key which leaves the
   spec empty once the schema is not enforcing, and a `base` of the
   literal string `"none"`); and `video.model.null` and
   `video.memory.null` (the videospec guard the review found was still
   a presence test). Every other row's `warn` answer is what the plan
   measured before the phase, including the eleven F5 rows which still
   answer `500 server error` with an exception recorded. `off` is
   measured on the whole table and not on a sample:
   `NestedSweepOffTestCase` subclasses the warn class, so it runs the
   identical 104 rows with `mode = 'off'`.
8. **Met.** Read in the tree rather than remembered.
   `_netdesc_safety_checks` (`external_api/instance.py:349`) still
   refuses a netdesc which is not a dict, still refuses one with no
   `network_uuid` — and now refuses one whose `network_uuid` is null,
   which is the change the item accounts for — and still refuses a
   malformed MAC through `util_network.valid_macaddr`. The disk bus
   check at `:732` is unchanged. The IDE refusal is still present at
   `:876` and still unreachable: the sweep's `disk.bus.ide` row answers
   `invalid disk bus ide` at `warn` and `off` and
   `disk[0].bus: Must be one of: sata, scsi, usb, virtio, nvme.` at
   `enforce`, and never `IDE disks are no longer supported`.
9. **Met.** `disk.empty` answers `400 disk specification must specify
   at least one of size or base` at all three modes, as do the other
   four spellings listed under item 7.
10. **Met.** `test_openapi_spec.py` passes, including
    `test_every_published_structure_or_bound_is_registered`, whose
    completeness is derived from the published specification. Every
    entry this phase changed — `video`, `disk`, `network` on
    `POST /instances` and `network` on the hotplug route, plus the
    `metadata` entry which changed only its comment — carries a comment
    saying what backs each constraint. The comparison ignores
    `description` and nothing else, which
    `_without_descriptions()`'s docstring justifies.
11. **Met, as amended: the script did not exist and was written in this
    step.** Steps 6 and 7 documented the specs by hand and nothing
    compared the result to `ARGTYPES`, which is precisely what this item
    exists to prevent — and the census had already found two live
    instances of that drift in tree (`usage.md` listing nine NIC models
    where the API reference listed eight, and `usage.md` and
    `config.DISK_BUS`'s description both still listing the `ide` bus
    which has been refused since v0.7). The new
    `shakenfist/tests/external_api/test_api_reference_specs.py` parses
    the three `### <spec>` sections of
    `docs/developer_guide/api_reference/instances.md` and asserts, in
    both directions, that the documented key set is the published key
    set; that each key's bracketed annotation is its published type,
    with `(enum)` meaning a string which publishes an `enum`; that
    `, required` and the fragment's `required` list agree; and that
    every published enum value appears verbatim in the bullet which
    documents it. It was mutation tested rather than merely run: adding
    a key to `ARGTYPES`, retyping `video.memory`, adding an undocumented
    enum member, dropping `network_uuid` from `required` and giving
    `network[].model` an enum each fail it, and the unmutated tree
    passes. The remaining half of the item — that the release note says
    the same thing — was checked by reading, and it does; it is also
    the document which had the `video.model` exception right when item
    5 did not.
12. **Met.** #528's body now describes the element-schema scope, links
    this plan and states that the `use_kwargs` residual was overtaken by
    phase 4 rather than abandoned, preserving what the original text
    asked for. It was then closed as completed, with a comment naming
    phase 4 (merged, `1c203b111` and `f1040a23b`) for the first half and
    this phase's seven implementation commits for the second, and naming
    what is deliberately not done.
13. **Met, as restated rather than closed.** #4167 item 1 was
    re-measured rather than reasoned about: at the default `enforce`,
    through this phase's own `SweepFixtureTestCase` fixture,
    `POST /instances {"uefi": null}` and `{"secure_boot": null}` both
    still answer 500 with an exception recorded and a body of
    `server error`, while omitting `uefi` or sending `true` reaches
    placement. Phase 6 closed the `cpus` and `memory` half of that item
    (`400 cpus: declared required but not supplied`). So something
    survives and the issue stays open, with a comment restating that
    the surviving scope is an explicit JSON `null` for a
    `required=False` scalar whose handler cannot take one — and that
    items 2 and 3 are closed, quoting their new answers.
14. **Not met, and cannot be met from a branch.** The `Merged` column
    stays `—` until the pull request merges, by the rule the column's
    own note states: every SHA there is the *merge commit* of a pull
    request, so that `<sha>^1..<sha>` is the whole of what it put on
    `develop`. Reading it from the branch, or from a path-filtered
    `git log`, is what #4222 exists to correct for phase 6. After the
    merge, take the SHA from the first-parent range —
    `git log --first-parent --oneline develop` — and record it beside
    the pull request number. The rest of the item is met: the Execution
    table reads `Complete` for phase 7 with a rewritten description, and
    `docs/plans/index.md` reads `8 of 9` with the status still
    `In progress`, because phase 8, the push audit, has not run.
15. **Met.** `pre-commit run --all-files` is clean, including the
    `Check plan statuses and index arithmetic agree` and
    `Check documentation links and anchors resolve` hooks and the mypy
    and unit-test hooks. The full `stestr` suite passes (4999 tests,
    121 skips) with the new documentation-consistency test in it.

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

## Progress

Written at the close-out, for a reader who was not here.

### What the phase did, in order

**Step 1, the census.** Before any schema was written, every key any
caller sends into a diskspec, networkspec or videospec was enumerated
from five sources: this tree, the client repository, the CI suites, the
documentation and the handlers themselves. The tables are above under
*The key and value census*. The census existed to test D41 and D43, and
it changed four decisions — see *What the steps found* below. It was
documentation only.

**Step 2, the compiler.** `_field()` gained an object branch which
builds a nested marshmallow schema from a rendered `properties` block,
honouring `required` and `additionalProperties: false`. `_ExactInteger`
landed in the same step, bound into `_SCALARS` so that no future branch
of `_field()` can build an integer which forgets it. Nothing rendered a
`properties` block yet, so no request changed its answer except through
the integer fix.

**Step 3, the schemas.** `ARGTYPES` gained `diskspec`,
`arrayofdiskspec`, `networkspec`, `arrayofnetworkspec` and `videospec`,
built from three module-level constants so a shape declared twice is
the same Python object. Every type came from the census and every enum
was read off the code which consumes the value. Still nothing declared
them, so still nothing changed for a caller.

**Step 3b, unplanned.** Step 3 reported that D44's mechanism had never
been built, and declined to overturn a committed decision on its own
authority, which was right. This step built it: `required` inside an
object fragment now means present *and not an explicit null*, the same
meaning phase 6 gave `required` at the top level.

**Step 4, the declarations.** The four structured declarations were
retyped and the contract moved. Two handler guards were added (D45's
size-or-base, and a value test rather than a presence test in
`_netdesc_safety_checks`), and `metadata` and `bound_claims` kept their
bare `dict` with a comment at each saying why.

**Step 5, the sweep.** `test_nested_sweep.py`: 104 rows of (spec, key,
sent value) driven through the real stack on both endpoints, at
`enforce`, at `warn` and at `off`. Sixty-one refused at `enforce` and
forty-three accepted, because a sweep which only proves things are
refused passes just as well against a server which refuses everything.
Two of the properties this step pins cannot be seen in a status code at
all and carry a test each: that a null `video` key is defaulted rather
than stored, and that a `float` of `"false"` does not float the
interface. Sixteen mutations in `tools/mutate-nested-sweep.sh`, none
survived; the harness now proves the unmutated tree is green before it
breaks anything, since otherwise an already-red tree reports every
mutation as caught.

**Step 6, the documentation.** The API reference now states, per spec,
which keys exist, which are required, what each value's type and enum
are, and that an undocumented key is refused. The release note names
the narrowings and, because the plan had been wrong about this, names
what is *not* narrowed. Three documentation bugs this phase
did not cause were fixed on the way.

**Step 7, cluster CI.** Four cases in
`cluster_ci_tests/test_api_validation.py`, against a deployed cluster.
Three refusals and — the one which matters most and is the easiest to
skip — a single instance carrying all twelve documented keys which must
actually boot, checked down to `df` showing a virtio disk.

**Step 8, the close-out.** This section, the definition-of-done audit
above, the master plan and index, and the issue tracker.

### What the steps found that the survey did not

Sixteen things, give or take, none of which are in the plan's *What the
survey found* section because none of them were visible from outside.

*From the census (step 1):*

1. **D41 came through unconditional.** No first-party caller sends an
   undocumented key into any of the three specs, and neither the CLI nor
   the ansible module has a key allowlist of its own, so the server's
   400 is the only place a typo can ever surface.
2. **D43 named six enums and only three are publishable.** Both `model`
   keys go into the libvirt domain XML with nothing in between, so the
   real vocabulary is the hypervisor's qemu build.
3. **D44's mechanism did not work.** Every compiled field is
   `allow_none=True`, so a required, string-typed nested `network_uuid`
   would still have accepted a null.
4. **D46's `strict=True` could not be used at all.** A query parameter
   is a string on the wire; applying the decision literally failed
   `test_blob_data_bounds` within a minute.
5. **Two of finding F5's attributions were wrong**, because the survey
   had read the status and not the code. The `address` fault is in
   `noneish` above `is_in_range`, and the `size` fault is
   `_safe_int_cast` rather than pydantic — which narrowed that row to a
   *non-numeric* string.

*From the compiler (steps 2 and 3):*

6. **D48 needed no code.** `_flatten_messages` already recursed through
   marshmallow's nested error dict and already produced `disk[0].size`
   by the same route it produced `side_channels[0]`. It is now pinned
   by tests rather than left to be rediscovered.
7. **The object branch has to be keyed on `properties`, not on the
   type.** Marshmallow's schema default is `unknown=RAISE`, so keying it
   on `type == 'object'` refuses every `metadata` key ever sent —
   mutation tested, which is D47 turning out to be load bearing rather
   than decorative. For the same reason the `additionalProperties` arm
   has both halves written out.
8. **The enum compiler was new.** Nothing in the tree published an
   `enum` before this phase, so `_field()` had no branch for one and all
   three published enums would have been documentation the server did
   not enforce — which would have made the stated reason for publishing
   the `vdi` enum false.

*From the declarations (steps 3b and 4):*

9. **The `_schema` sentinel regression.** Wrapping a spec in
   `fields.Nested` made a non-mapping element answer
   `network[0]._schema: Invalid input type.`, leaking marshmallow's
   internal sentinel into a caller-facing message where F1 had already
   blessed `network[0]: Not a valid mapping type.` as good. The
   flattener now collapses the schema-level key onto the container's own
   name, and the two pre-existing tests asserting that string pass
   unchanged — which is the evidence the case did not move.
10. **The `warn`-mode guard gap.** The schema refusing a null
    `network_uuid` is not enough, because `warn` and `off` are the
    operator's rollback and bypass it, and a rollback which hands back
    the bug the phase just closed is not a rollback. So
    `_netdesc_safety_checks` tests the value rather than the key.
11. **`noneish` cannot be asked about a size.** It lowercases whatever
    it is handed, so the size-or-base guard needed
    `_diskspec_value_absent()` around it rather than calling it
    directly on the integer the guard exists to protect.
12. **A narrowing the plan never named: `blob_uuid` inside a
    diskspec.** `additionalProperties: false` refuses a caller-supplied
    one, and the handler genuinely acted on it. The documented spelling
    `base: "sf://blob/<uuid>"` reaches the identical branch, so nothing
    a caller is told to do stops working.

*From the sweep (step 5):*

13. **A third class of `warn`-mover.** The plan expected two rows not to
    roll back and there are seven, in three classes: the null
    `network_uuid` on both routes, the four spellings of a diskspec
    which asks for nothing, and one nobody had seen — a diskspec
    carrying *only* a typo'd key has neither a size nor a base, so the
    size-or-base guard answers it at every mode rather than the schema
    answering it at `enforce`.
14. **D48 has a limit, recorded rather than fixed.** "One fact, one
    message" holds within a nesting level and not across them: an
    omitted required *parameter* answers `missing-required`, an omitted
    required *property* answers `type-mismatch`. Rewording the nested
    detail would trade a cosmetic inconsistency for a telemetry one.

*From the documentation (step 6):*

15. **`usage.md`'s worked example had answered 400 as written.** It
    showed `-D size=8,base=cirros,bus=ide,type=cdrom`, and `ide` has
    been refused since v0.7. `config.DISK_BUS`'s own description listed
    `ide` too and pointed at the libvirt documentation for "the full
    list", which is not the list we accept. The API reference also
    typed `network_uuid` as a uuid (a network *name* resolves there, and
    the CI suite relies on it) and typed `video.vdi` as a plain string
    when it is the one real enum in the vocabulary.

*From the close-out (step 8):*

16. **D46's rewrite had two more stale dependents outside the plan**,
    both fixed here: `base.py`'s `video.memory` comment still called the
    typing "a narrowing with an ordering obligation attached", and a
    test docstring still quoted D49's retracted claim in the present
    tense. **Definition-of-done item 5 named four accepted values and
    there are five** — it had reasoned from half of D43 and omitted
    `video.model`. And **item 11's script did not exist**; it was
    written in this step as
    `shakenfist/tests/external_api/test_api_reference_specs.py` and
    mutation tested.

The pattern in 3, 4, 13 and 16 is the phase's real lesson, and it is
recorded in the second amendment as well as here: **a decision rewritten
mid-phase invalidates every other statement which was reasoning from
it, and nothing in this plan's structure made those statements
findable.** Four steps rediscovered the same drift independently before
anyone wrote it down, and the close-out found two more instances after
the corrections had supposedly been made. A phase which amends a
decision should grep for the decision's number and for its *claims*,
not only fix the decision.

### What is left

* **The `Merged` column** of the master plan's Execution table, which
  cannot be filled until the pull request merges and must then be taken
  from the first-parent range rather than from a path-filtered log. This
  is definition-of-done item 14, and it is the only item recorded as not
  met.
* **Phase 8**, the push audit, which runs over the accumulated diff of
  every phase of this plan rather than over this one's.
* **[#4223](https://github.com/shakenfist/shakenfist/issues/4223)**, the
  null-reference lookup, filed by this phase's survey and deliberately
  not fixed by it. The only path a caller can reach is closed twice over
  now, by the schema and by the handler guard, but the function is still
  wrong for the next caller who reaches it another way.
* **[#4167](https://github.com/shakenfist/shakenfist/issues/4167)**,
  reduced to `uefi` and `secure_boot` as an explicit `null` — scalars,
  `required=False`, reached by no element schema — and re-measured at
  `enforce` during this step as still a recorded 500.
* **[#4236](https://github.com/shakenfist/shakenfist/issues/4236)**, the
  ansible collection's `video` parameter, which declares a string and
  sends one where the API wants an object. Broken since enforcement was
  turned on in phase 4 rather than by this phase, recorded as census
  finding N4, and filed during review.
* **[client-python#398](https://github.com/shakenfist/client-python/issues/398)**,
  coercing a videospec `memory` in `apiclient.py` beside the disk-size
  block it mirrors. A tidy-up and not a release gate: D46's width means
  the documented CLI invocation keeps working either way, and the only
  cost of not doing it is a videospec whose memory is stored as a
  string.
