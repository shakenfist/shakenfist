# Scheduler reservations phase 6: affinity model and the 3565 disposition

## Prompt

Before responding to questions or discussion points in this
document, explore the codebase thoroughly. Read the scheduler's
candidate pipeline end to end (`shakenfist/scheduler.py`,
`find_candidates()` especially), the functional affinity test,
and the audit events the scheduler publishes -- those events are
the primary evidence for everything this phase decides, and they
are readable from a live cluster. Ground answers in what the code
does today rather than in what the plan documents say it does:
three of this phase's inputs turned out to be stale, and two of
them were disproved by their own quoted numbers.

Do not treat the `507 ... sufficient_idle_cpu` CI failures as
this phase's evidence. They come from the admission stage and
belong to `PLAN-ci-cloud-sizing`. See F6.

<!-- shared-block: plan-file-conventions v1 -->
Plan file conventions (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-file-conventions.md`):

- All planning documents live in `docs/plans/`.
- Detailed planning gets one plan file per phase. Phase files are
  named for their master plan, sit in the same directory as it,
  and append `-phase-NN-descriptive` before the `.md` extension.
- The master plan tracks its phases in a table under its Execution
  section:

  | Phase | Plan | Status |
  |-------|------|--------|
  | 1. Schema migration | PLAN-thing-phase-01-schema.md | Not started |
  | 2. Public API | PLAN-thing-phase-02-api.md | Not started |

- One commit per logical change, and at minimum one commit per
  phase. Unrelated changes are not batched into a single commit.
  Each commit is self-contained: it builds, passes tests, and has
  a message explaining what changed and why.
<!-- shared-block-end -->

## Planning effort

Planned at high effort, with a high-effort review expected on the
model change (steps 3 and 4). The survey did most of the work and
found that the phase's central question had already been answered
by a traced CI run nobody had folded back into the plan
documents. What remains is a judgement about what a soft
guarantee is allowed to promise, which is cheap to decide here
and expensive to relitigate once an API has shipped it.

## Situation

Phase 6 has carried the same shape since the plan was cut in May:
adopt a binary affinity model, deprecate arbitrary numeric
weights, and close issue #3565, the `test_affinity` flake whose
title is "soft affinity loses to resource filters under suite
concurrency".

Two corrections have accumulated on it since. The 2026-08-16
correction to decision D6 established that the ranking precedence
D6 asked for had already landed (PR 3722), that #3565 had
recurred five times since, and that closing it therefore needed a
decision the plan had not taken: **may a soft affinity preference
bid against a hard admission ceiling?** Three positions were put
up for this phase to choose between. The 2026-08-19 correction,
restated on 2026-08-22, added a competing explanation -- issue
#3813's demand guard destroying the scheduler's ability to spread
a burst -- and instructed this phase to rule it out before
spending its decision budget.

Both corrections are honest and both are now overtaken. Phase 4a
fixed #3813 on 2026-08-24. On 2026-08-26 a fully traced
occurrence of #3565 was posted to the issue, and it shows a third
mechanism which is neither of the two the plan documents argue
about. That trace is the single most important input to this
phase and it is summarised in finding 3 below.

## Mission and problem statement

Decide what soft affinity promises, make `test_affinity` assert
that promise rather than a stronger one the product does not
make, and build the binary affinity model D6 asked for and nobody
has yet written.

Separate the two things phase 6 has always had tangled: the
affinity *model* is a design change with an API surface, and
#3565 is a test asserting a guarantee that does not exist. They
share a subject and nothing else.

## Scope

In scope:

- Binary affinity: `require_with_tag` / `require_without_tag` as
  filters, `prefer_with_tag` / `prefer_without_tag` as a +/-1
  ranking term.
- A mechanical mapping from the existing weighted form, for one
  transition release.
- The disposition of #3565, and the `test_affinity` rewrite that
  implements it.
- The audit-event surface those assertions read, if it needs
  widening.
- Documentation of what soft affinity does and does not promise.

Out of scope:

- **The `507 ... sufficient_idle_cpu` refusal family** (#3772).
  Admission, not ranking. `PLAN-ci-cloud-sizing` owns it. F6.
- **The CI topology.** It lives in `shakenfist/actions` and is
  demonstrably undersized; D6 said no scheduler change will make
  `test_affinity` reliable while it stands, and that is still
  true. `PLAN-ci-cloud-sizing` owns it.
- **Softening the admission ceiling.** Declined rather than
  deferred; see F7.
- **The forced-candidate retry defect** found by the survey.
  Recorded as an issue, see finding 6 and F8.

## What the survey found (2026-08-29)

Surveyed against shakenfist `5af155827`. Five of the eight
findings contradict something a plan document currently asserts.

**1. The binary affinity model does not exist, at all.**
`require_with_tag`, `require_without_tag`, `prefer_with_tag` and
`prefer_without_tag` appear nowhere in the codebase -- not in
the scheduler, the API, the schema or the client. Affinity today
is `inst.affinity`, a dict of tag to number, summed per candidate
into a score (`scheduler.py:529-600`): for each instance already
on the node, each requested tag it carries contributes its
requested value. So D6's headline deliverable is entirely
unbuilt, and this phase is a first implementation rather than a
migration. The plan's phase stub is accurate about this; it is
recorded because the volume of correction elsewhere in this
document might otherwise suggest the whole stub is stale.

**2. D6's line references have all moved.** D6 cites
`scheduler.py:473-481` for the CPU admission filter,
`scheduler.py:611-631` for the affinity ranking and
`scheduler.py:260` for the hard ceiling. The current locations
are `:487-501`, `:529-600` and `:187-256`. The claims those
references support are still true -- admission does run before
affinity scoring, and `hard_max_cpus` is still absolute -- but
anyone checking D6 against the tree will bounce off the line
numbers first.

**3. #3565's traced mechanism is neither of the two the plan
argues about.** This is the finding that reshapes the phase.

A fully traced occurrence was posted to the issue on 2026-08-26,
after phase 4a fixed #3813 on 2026-08-24, so it is evidence from
a cluster where the spreader works. In it the candidate set had
already collapsed to **one node** by the time affinity was
scored. inst3, which requested anti-affinity (`{'first-node':
-100}`), scored that node `-100` -- correctly, having found and
matched inst1's tag -- and placed there anyway, because a scorer
given one candidate has nothing to do. The issue's own summary is
exact:

> the mechanism is slightly stronger than "soft affinity loses to
> resource filters under load": here it did not lose a tiebreak,
> it was **never consulted** in any meaningful sense.

The other half matters just as much. inst2 landing on inst1's
node -- the half of the assertion that *passed* -- was the same
forced choice. The test's pass and its failure had the identical
cause, so the run tells us nothing about affinity in either
direction.

That rules out the competing explanation the 2026-08-19
correction asked this phase to eliminate: it is not lost
spreading, and it is not affinity losing a tiebreak to load
ordering. It is the candidate set emptying to one before ranking
begins.

**4. The two CI topologies fail differently, and the difference
is diagnostic.** `slim-primary` produces the wrong-node signature
(#3565 proper -- instances that should share a node do not).
`slim-tier` produces total refusal (`507 ...
sufficient_idle_cpu`, #3772), where the create never happens.
Sampling merge-queue runs on 2026-08-28 and 2026-08-29 shows both
live: in run `33208761280`, `Debian 12 cluster` passed
`test_affinity` while `Debian 12 tier` failed it with
`schedule at stage sufficient_idle_cpu`; run `33226077736` shows
the tier passing it while the same stage refuses other creates.
`PLAN-ci-cloud-sizing` reaches the same split independently and
calls #3772 an umbrella hiding two distinct causes.

Two consequences. A green `test_affinity` on one topology is not
evidence the scheduler is right, and a red one on the other is
not evidence it is wrong. And while the tier can produce total
refusals, no measurement campaign run in CI can characterise
#3565, because the failure that reaches the assertion is not the
failure under study.

**5. The binding stage has moved from CPU to memory.**
`PLAN-ci-cloud-sizing` records, from a 2026-08-26 comment on
#3565, that the most recent fully traced occurrence has the
affinity target surviving `sufficient_idle_cpu` and being dropped
at `sufficient_idle_memory`. D6's analysis is written entirely
around the CPU stage. The mechanism is unchanged -- an admission
filter empties the candidate set before ranking -- but any fix
aimed specifically at CPU admission would now miss.

**6. A retry against an already-refused node, whose site the
tree does not confirm.** From the same traced run: both instances
were first refused by the capacity guard on the single surviving
candidate, and the retry went to that same node. An earlier draft
of this finding described that as "re-forcing the candidate set
rather than reopening it", and that description does not survive
a reading of the code; the correction is recorded here rather
than quietly dropped, because the finding is the input to an
issue somebody else will have to act on.

Neither retry in the tree behaves that way. The create path's
second walk does re-walk the same list, but only when at least
one candidate was refused **on demand alone**, and it waives the
demand guard when it does (`external_api/instance.py:924-940`)
-- so retrying the same node is the designed behaviour, it can
succeed, and the first refusal did buy something. The preflight
path does the opposite: its redirect rebuilds the list from every
node except the current one
(`operations/node_inst_netdesc_op.py:172-180`). The one site that
forces a node an earlier pass has already chosen is preflight's
opening call, `find_candidates(inst,
candidates=[config.NODE_UUID])` (`:156`), which runs on the node
the create path just placed on.

That is a candidate mechanism, not a confirmed one. This is the
only finding here whose evidence is an issue comment rather than
the tree, and the trace has not been re-read against those three
sites -- step 2 must do that before filing. Out of scope for this
phase either way, per F8.

**7. `test_affinity` is itself a user of the deprecated weighted
form.** It requests `{'first-node': 100}` and `{'first-node':
-100}` (`cluster_ci_tests/test_scheduler.py:80,99`). D6 asked
whether anything beyond the CI suite uses numeric weights and got
no answer; the CI suite is unambiguously a user, so the
transition mapping has a test exercising it from day one, and the
weighted form cannot be removed in the same change that adds the
binary one.

**8. The audit events already carry what the test needs.** The
2026-08-26 trace makes this point explicitly -- the events
"contain enough to distinguish 'the scheduler got affinity wrong'
from 'the scheduler had one candidate'". `schedule final
candidates` publishes the ordered list, `schedule have highest
affinity` publishes the per-candidate scores and the winning
tier, and each filter stage publishes its dropped map. The test
already fetches exactly these: `_add_scheduler_detail()`
(`test_scheduler.py:19-35`) pulls every event whose message
starts with `schedule` and attaches it. So the audit surface
needs no widening for the rewrite, which makes F2 much cheaper
than it looked.

### Corrections made at source

As part of the planning commit:

- The master plan's phase 6 stub loses the 2026-08-19/08-22
  correction's instruction to rule out the lost-spreading
  mechanism, because finding 3 has ruled it out, and gains the
  traced mechanism in its place.
- D6's line references are updated, and a dated note records
  that the stage which binds is now memory rather than CPU.

Not corrected here, deliberately: D6's three positions stay as
written. F2 and F7 dispose of them, and a decision record should
show what was on offer.

## Decisions

**F1. The phase is two independent pieces, and they ship
separately.** The affinity *model* (findings 1 and 7) is a design
change with an API surface and a deprecation window. #3565
(finding 3) is a test asserting a guarantee the product does not
make. They have been tangled since the plan was cut because they
share a subject, but neither blocks the other: the test rewrite
is correct against today's weighted model and stays correct
against the binary one, and the model change is worth making
whether or not the test ever flaked. Steps 1 and 2 are the test;
steps 4 to 6 are the model. Either can land first. Step 3 -- the
validator 500 -- belongs to neither and is gated by neither; it
is a one-clause fix to a live fault, split out of the model so it
does not wait behind an API surface and a back-brief gate.

**F2. #3565 is closed by the issue's candidate fix 2 -- assert
from the audit events -- and not by any of D6's three
positions.** This is the decision most likely to be argued with,
because it declines to change the scheduler in response to a
scheduler bug report.

The argument is finding 3. In the traced run the candidate set
was one node. Ask what each of D6's positions would have done
there. Position 1 (hard require only) turns the create into a
507, which the issue itself identifies as "the same ejection with
a different traceback" -- a placement flake traded for a #3772
refusal. Position 2 (soften the ceiling above a threshold
affinity score) has nothing to soften: the affinity target *was*
the surviving candidate and the instance was placed on it.
Position 3 (accept that co-location is not guaranteed under
concurrency, and change what the test asserts) is candidate fix 2
under another name, and is the one the evidence supports.

So the test asserts what soft affinity actually promises: that
the scheduler *scored* the affine node highest among the
candidates it had, or legitimately had no choice. It stops
asserting an outcome -- final co-location -- that no
documentation claims and the code has never guaranteed.

The honest framing is that #3565 was mostly a specification bug.
The product never promised co-location under contention; the test
assumed it did, and every investigation since has been looking
for the scheduler defect behind an assertion that was too strong.

**F3. The test skips, rather than passes, on a degenerate
candidate set -- measured at affinity-scoring time.** If the
scheduler had one candidate, the run carries no information about
affinity and must not be counted as evidence that affinity works.

*Where the count comes from matters, and two obvious answers
are both wrong.* Each was proposed in turn and each would have
skipped the test on every healthy run. Recorded because the failure
is silent -- a skip is green -- and because the second
survived a round of review.

`schedule final candidates` is published at the end of the
pipeline, after `candidates = narrowed` (`scheduler.py:658`)
has reduced the list to the winning affinity tier. On a healthy
cluster where inst2's affinity works exactly as intended that
tier is the single node carrying the tag, so the final list
holds one candidate. That is exact for the inst2 assertion and
only for it: inst3 requests `{'first-node': -100}`, whose winning
tier is every node *not* carrying the tag -- two of the three the
test requires. A source that is right for one of the two
assertions and wrong for the other is unusable, which is why the
event is ruled out rather than qualified.

`schedule have highest affinity` fixes that and then fails the
same way through a different door, because it is published once
per `find_candidates()` call and **that function runs more than
once per instance on the ordinary happy path.** The create path
calls it unforced (`external_api/instance.py:867`); the
preflight operation then calls it again against the assigned
node alone (`operations/node_inst_netdesc_op.py:156`,
`candidates=[config.NODE_UUID]`); and the create path forces a
single candidate itself whenever the instance is already placed
(`:869`). The affinity loop runs over whatever `candidates`
holds, so a forced call yields an `affinity_detail` of exactly
one entry -- on every successful create, on a healthy cluster.
A test taking "the" event, or the most recent one, which is
what a descending-order fetch hands back first, would skip
forever.

The count therefore comes from `len(affinity_detail)` in the
`schedule have highest affinity` event **belonging to the
unforced scheduling pass**. The scheduler publishes a clean
discriminator: `schedule inputs` carries `'forced_candidates':
bool(candidates)` (`scheduler.py:438`), so the test finds the
`schedule inputs` whose `forced_candidates` is false and takes
the `schedule have highest affinity` carrying the **same
`request_id`**, rather than any event matching the message.
Pairing on `request_id` rather than on adjacency matters: the
rows come back ordered by float timestamps which can tie, whereas
the create path takes exactly one of its two `find_candidates()`
branches per request (`external_api/instance.py:866-870`), so the
id identifies the unforced pass outright -- and the preflight
call, running in the queue daemon with no flask request, carries
no `request_id` at all (`eventlog.py:82-86`).

A pass on a single-candidate run is exactly the false green
finding 3 describes -- inst2's co-location "passing" for the
same reason inst3's anti-affinity failed. `skipTest` with the
candidate count in the message keeps the failure visible in CI
output without asserting something the run cannot support.

This is deliberately stricter than a test that merely stops
failing. Making the flake go away is easy and would leave the
suite claiming coverage it does not have.

**F4. Build the binary model, and keep the weighted form working
through a mechanical mapping for one release.** Per D6:
`require_*` become filters applied with the hard admission
filters, `prefer_*` contribute +/-1 per matching co-located
instance in the ranking term that already exists. Weighted specs
map positive to `prefer_with_tag` and negative to
`prefer_without_tag`, losing the magnitude.

*The scoring is count-proportional, not set-membership, and the
rationale is corrected to match (2026-08-29).* This decision
originally justified dropping caller magnitudes on the grounds
that a magnitude is "summed across an unbounded number of
co-located instances" -- but `+/-1 per matching instance` is
summed across that same unbounded number, so the stated reason
argued against the mechanism the decision adopts. The genuinely
bounded alternative is set-membership: `+/-1` if any matching
instance is present, regardless of count.

Count-proportional is chosen. A node carrying five instances of
an affinity group really is more "with the group" than one
carrying a single instance, and for the anti-affinity direction a
node carrying five instances you asked to avoid really is worse
than one carrying one. Set-membership discards exactly that
signal, which is the one a pack-or-spread request is about.

The corrected rationale for dropping caller magnitudes is
therefore not the summation but the scaling: a caller choosing
`100` rather than `1` is choosing a multiplier on a neighbour
count they cannot predict, so the two are not comparable to each
other or to anything else. Removing the multiplier leaves a
quantity that is defined -- how many matching neighbours a node
has -- which is what the ranking should be comparing. Step 1's
inst3 mutation test is written against this choice.

Finding 7 means the mapping is exercised from the moment it
exists, because `test_affinity` is a weighted caller. Whether
that test moves to the binary form in this phase or stays on the
weighted one as living proof the mapping works is left to step 5;
there is a real argument for the latter.

**F5. No new measurement campaign.** The 2026-08-19 correction
asked this phase to establish which mechanism operates before
spending its decision budget. Finding 3 establishes it, from a
trace taken after phase 4a's fix, at a level of detail no
sampling campaign would improve on -- it has the per-candidate
affinity scores and the placement decision for a single run.
Finding 4 adds that CI cannot produce the measurement anyway,
because on one topology the failure under study never reaches the
assertion.

Spending a week of sfcbr sampling to re-derive a conclusion
already sitting in the issue would be the same mistake this plan
has now made twice in a fortnight, in the other direction:
preferring a document's account of the evidence to the evidence.

**F6. The 507 family is not adopted.** `sufficient_idle_cpu`
refusals come from an admission stage that runs before ranking
and reads the capacity counters. When it refuses every candidate,
no ranking model would have helped. #3772 and the CI topology
belong to `PLAN-ci-cloud-sizing`, which has the better analysis
of both. This is written down because the family is the loudest
scheduler-shaped signal in CI and is repeatedly mistaken for
evidence about ranking -- including by this session, before the
survey.

**F7. Softening the admission ceiling is declined, not
deferred.** D6 offered it as the only position that makes the
co-location case work, at the cost of admission no longer being a
pure capacity question, and with a bound nobody had chosen.
Declining it: the property that admission answers "does this fit"
and nothing else is what makes the capacity counters trustworthy,
and phase 3 spent considerable effort making a single guarded
UPDATE the sole authority on that question. A preference that can
raise a ceiling would put a ranking input inside a transaction
whose correctness argument depends on it being arithmetic. If a
future workload genuinely needs guaranteed co-location, the
honest mechanism is a hard `require_with_tag` plus enough
capacity, not a soft preference with leverage.

Recorded as declined so a later phase does not rediscover it as
an open question.

**F8. The retry behaviour gets an issue, not a fix -- and the
issue has to be pinned first.** Whatever finding 6 turns out to
be, it is in the placement retry paths rather than the affinity
model, and fixing it inside this phase would widen a phase that
is already two phases wearing one number. But finding 6's
mechanism is not confirmed against the tree, so step 2 pins it to
a call site before filing and files only what the trace
substantiates: a wrong issue is worse than none, and this plan's
standard everywhere else is grounding claims in what the code
does today. Reference it from #3565's closure so the connection
is not lost, and leave it.

**F9. A hard `require` that ejects every candidate answers 409,
not 507.** Placing the `require_*` stage among the admission
filters means it publishes its dropped map through
`_log_and_raise_on_error()`, which raises `LowResourceException`
once the candidate list empties (`scheduler.py:386-392`). The
create path catches exactly that and answers **507 "insufficient
resources"** after enqueuing a delete
(`external_api/instance.py:871-876`). Left alone, a
`require_with_tag` naming a tag no co-located instance carries
would tell the caller the cluster is full -- which is false, and
which is precisely the complaint F2 makes against D6's position
1, where a 507 is "the same ejection with a different traceback".
The definition-of-done bullet, phrased as "a no-candidate refusal
naming that stage", would have been satisfied by that wrong
answer, so it is tightened below to name the code.

So the stage raises `AffinityConstraintUnsatisfiable`, **a
subclass of `LowResourceException`**, and the create path gains
an `except` clause for it placed *before* the existing one.
Python matches `except` clauses in order, so that ordering is the
entire mechanism and is the easy thing to get wrong. It answers
409, naming the constraint and the stage: the request is
well-formed and conflicts with the current state of the cluster,
which is how this API already uses 409 for lifecycle refusals
(`external_api/instance.py:1228` and five siblings). The instance
is still deleted -- it exists only because it is created before
scheduling runs, and leaving it would leak.

Subclassing rather than introducing a sibling exception is
deliberate. Preflight runs this stage too, and catches
`LowResourceException` to redirect to other nodes
(`node_inst_netdesc_op.py:159-180`). Redirecting is exactly right
for a constraint that another node may satisfy, so that path
should keep working untouched; a sibling exception would escape
it as a traceback.

## Design: where the binary form lives

Affinity is not a first-class instance field. It is instance
metadata under the reserved key `affinity`
(`instance.py:247,736`), validated at
`external_api/instance.py:1395-1406` as a JSON dict whose values
must be integers. The scheduler reads it as `inst.affinity` and
sums per-tag contributions.

The binary form therefore has to be a new *value shape* under the
same key, not a new key, because a second key would let a caller
supply both and mean nothing coherent. The shapes are
distinguishable by value type without ambiguity:

```
# weighted (existing, deprecated for one release)
{'affinity': {'first-node': 100, 'db': -50}}

# binary (new)
{'affinity': {'prefer_with_tag': ['first-node'],
              'prefer_without_tag': ['database-tier'],
              'require_with_tag': ['web-frontend'],
              'require_without_tag': ['batch-worker']}}
```

Every tag named here is an **instance** tag -- the `tags`
metadata key of instances already placed on a candidate node
(`instance.py:783-784`, consumed at `scheduler.py:568-587`) --
and not a property of the node. Shaken Fist has no node
capability tags, so an example like `require_with_tag: ['ssd']`
would teach a model that does not exist. This is why
`test_affinity` has to create inst1 with `{'tags':
['first-node']}` before any affinity request means anything.

A dict whose values are integers is the old form; a dict whose
keys are the four reserved names and whose values are lists is
the new one. Anything else must be a 400. That check is
mechanical, which matters because the validator is the only place
a caller gets told they got it wrong.

*It cannot tell them today.* The existing validator coerces with
`int(dv)` inside a `try` that catches only `ValueError`, and
`int()` raises `TypeError` -- not `ValueError` -- for a list, a
dict or `None`. Confirmed at the interpreter: `int(['a'])`,
`int({'x': 1})` and `int(None)` all raise `TypeError`. So the new
binary shape, whose values are lists, posted against today's
server produces an uncaught `TypeError` and a **500**, and
`_validate_instance_metadata` is shared by instance create
(`:797`) and both metadata endpoints (`:1375`, `:1425`), so all
three paths reach it -- today, for any caller who guesses the
new syntax early. Step 3 widens the except clause rather than
layering a new shape on top of the hole, and it is its own step
for that reason: the fix depends on none of this phase's
decisions and can land ahead of the model. Step 2 files it,
because it is a live 500 on a public API and should be tracked
whether or not this phase lands.

One related imprecision worth carrying: `int()` also accepts
floats and numeric strings, so "values must be integers"
describes the intent rather than the coercion.

The mapping in F4 is then: for each `tag: n` in a weighted spec,
emit `prefer_with_tag: [tag]` when `n > 0` and
`prefer_without_tag: [tag]` when `n < 0`. `n == 0` maps to
nothing, which is what it already means.

**The hard filters keep the scorer's namespace scope.** Today's
scorer skips co-located instances in another namespace
(`scheduler.py:574-580`, `'skipped': 'different namespace'`),
which for a preference is plainly right -- you cannot prefer
what you cannot see. For `require_without_tag` the choice is
sharper, because inheriting the scope means "never place me
beside an instance tagged `noisy`" actually means "never beside
one of *my own*", which is weaker than the name suggests.

It inherits the scope anyway. The alternative makes a placement
outcome depend on other tenants' instance tags, which is a
probe: a caller could learn what tags exist in namespaces they
cannot read by watching which creates get refused. That is a
change to the trust boundary described in
`docs/developer_guide/security_model.md`, and it is a much
larger decision than an affinity model should be allowed to
make in passing. If cross-tenant isolation is ever wanted, it
should be built as isolation, with its own threat model, and
not fall out of a scheduling filter.

Step 6 must therefore document `require_without_tag` as a
within-namespace constraint in as many words, because the first
operator to reach for it will read it as isolation.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent | Status |
|------|--------|-------|-----------|---------------------|--------|
| 1 | high | opus | worktree | (shakenfist) Rewrite `test_affinity` onto the audit events, per F2 and F3. In `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_scheduler.py`, keep the three-instance setup unchanged and replace the two placement assertions (`:124-131`, whose `['node']` arguments are on `:125` and `:129`) with assertions read from the scheduler's own events. The events are already fetched by `_add_scheduler_detail()` (`:19-35`), which filters `get_instance_events()` to messages starting with `schedule` -- factor its fetch out into a helper returning the events so both the detail-attachment and the assertions use one read, rather than fetching twice. Assert, for inst2: that `schedule have highest affinity` scored inst1's node in the winning tier. For inst3: that inst1's node was **not** in the winning tier. Then **skip, do not pass**, when the scorer had fewer than two candidates to choose among, with the count in the skip message (F3). Take that count from `len(affinity_detail)` in the `schedule have highest affinity` event **of the unforced scheduling pass**, located by finding the `schedule inputs` event whose `forced_candidates` is false (`scheduler.py:438`) and taking the affinity event carrying the **same `request_id`**. Pair on `request_id`, not on adjacency: it is a field of `EventReadRow` (`schema/event.py:84`), populated from `FLASK_REQUEST_ID` (`eventlog.py:82-86`) and returned to clients by `row.model_dump()` (`external_api/base.py:1479-1484`); the create path takes exactly one of its two `find_candidates()` branches per request (`external_api/instance.py:866-870`), so the id identifies the unforced pass outright, while the preflight call runs in the queue daemon with no flask request and no `request_id` at all. Adjacency is the fallback only when `request_id` is absent, and it is fragile because the rows are ordered by float timestamps that can tie. Two wrong sources, both of which skip unconditionally, are ruled out in F3 and must not be reintroduced: `schedule final candidates` (post-narrowing, holds one node whenever affinity works), and any `schedule have highest affinity` matched by message alone (`find_candidates()` runs several times per create, and the forced calls each publish one with a single entry). **The fetch must also be widened.** `_add_scheduler_detail()` calls `get_instance_events()` with no limit, and that endpoint defaults to `limit=100` (`external_api/instance.py:1203`) over rows ordered by timestamp descending (`mariadb.py:6139-6146`) -- so it returns the *newest* hundred, while the create-path scheduling events are the *oldest* an instance has, behind all its networking, image, boot and agent events. Pass `event_type='audit', limit=1000` explicitly. Both, not one: 1000 is the endpoint's hard cap (`:1193-1195`, `{'minimum': 1, 'maximum': 1000}`), so the audit filter is what keeps a busy create inside that ceiling rather than an optional tidiness -- and since the brief also requires `self.fail()` when the unforced pair is missing, a create that emits more than 1000 events would otherwise become a test failure blamed on affinity. Two call sites in the suite already use this idiom: `get_instance_events(inst['uuid'], event_type='mutate', limit=1000)` (`cluster_ci_tests/test_events.py:136-137`), and `cluster_ci_tests/test_namespace_claims.py:415-421`, whose docstring makes exactly this argument about a default read pushing an old event off the end. If the unforced pair is not found, `self.fail()` with that reason rather than skipping: a missing event means the read was wrong, not that the run was degenerate, and a skip would hide it. `affinity_detail` has two shapes -- the normal `{'score', 'instance_count', 'considered'}` (`:596-600`) and `{'score': 0, 'reason': 'node row not found'}` (`:548-551`) for a candidate whose node row could not be read -- so count its entries but do not index inside them unconditionally, or a transient node-row failure becomes a `KeyError` and an unreadable test error instead of a diagnosable skip -- a single-candidate run carries no information and a green result there is the false pass finding 3 describes. Read the 2026-08-26 comment on issue #3565 first: it contains the exact event payloads from a failing run, including an `affinity_detail` that scored a node `-100` and placed there anyway, and it is the specification for what these assertions must distinguish. Do not assert final co-location anywhere -- that is the guarantee F2 establishes the product does not make. This runs only on `merge_group` (`docs/developer_guide/coding_rules.md:341-352`), so exercise it against sfcbr before proposing the commit. Commit subject: `tests: assert what soft affinity actually promises.` | Not started |
| 2 | low | sonnet | none | (GitHub) The #3565 disposition, once step 1 has merged. Comment on #3565 recording F2: the traced 2026-08-26 run shows a single-candidate set, so affinity was never consulted, and both halves of the assertion had the same cause; the issue is closed by candidate fix 2 rather than by a scheduler change; D6's three positions are disposed of by F2 and F7. Close it. Then deal with the retry behaviour from finding 6, and **pin it to a call site before filing** (F8). Re-read that comment's event payloads against the three sites finding 6 names: the create path's demand-waiving second walk (`external_api/instance.py:924-940`), the preflight redirect that rebuilds the candidate list excluding the current node (`operations/node_inst_netdesc_op.py:172-180`), and preflight's opening forced call against `config.NODE_UUID` (`:156`), which runs on the node the create path has just chosen. File the issue against whichever the trace matches, quoting the payloads and naming the file and line. If none of them matches, file it as an observation that says so in as many words rather than asserting a mechanism -- a wrong issue is worse than none. Cross-reference it from #3565's closing comment. Also file the validator defect from the Design section as its own issue -- `_validate_instance_metadata`'s `except ValueError` does not catch the `TypeError` that `int()` raises for a list, a dict or `None`, so an affinity dictionary *value* which is a list, a dict or `None` -- `{'affinity': {'first-node': ['a']}}` -- returns 500 rather than 400 from instance create and both metadata endpoints. Be precise about the level in the issue: a malformed *outer* `affinity` value is already refused correctly, and only the inner per-tag coercion leaks -- because it is a live fault on a public API and should be tracked whether or not this phase lands. Reference it from step 3, which fixes it. Also comment on `PLAN-ci-cloud-sizing`'s tracking of #3565 that its "needs a disposition in phase 0 before phase 4" is now satisfied. Include *(Triage assisted by Claude Code)*. | Not started |
| 3 | low | sonnet | worktree | (shakenfist) The validator 500, on its own. Widen `_validate_instance_metadata`'s `except ValueError` to `except (TypeError, ValueError)` (`external_api/instance.py:1403-1406`). `int()` raises `TypeError`, not `ValueError`, for a list, a dict or `None`, so an affinity dictionary *value* of that shape escapes the handler and returns **500** today from instance create (`:797`) and from both metadata endpoints (`:1375`, `:1425`). Get the level right: a malformed *outer* `affinity` value is already refused correctly -- a list by `isinstance(value, dict)` (`:1398-1400`) and `None` by `if not value` (`:1387-1388`) -- and only the inner per-tag coercion leaks. Unit tests in `shakenfist/tests/` for the list-valued, dict-valued and `None`-valued inner cases, which are the three that 500 now. This step is deliberately separate from the binary model below and depends on none of this phase's decisions: it is a live 500 on a public API, the fix is one `except` clause, and it should not wait behind an API surface, a new scheduler stage and a back-brief gate. It can land as soon as step 2 has filed the issue it closes. Commit subject: `Refuse malformed affinity values with a 400.` | Not started |
| 4 | high | opus | worktree | (shakenfist) The binary model. Per F4 and the Design section: accept the new value shape under the `affinity` metadata key, validate it at `external_api/instance.py:1395-1406` alongside the weighted form (four reserved keys, list-of-string values, 400 on anything else), and consume it in `scheduler.py`. Step 3 has already widened the `except` clause in that same function; build the new-shape validation beside it rather than repeating it. `require_with_tag` / `require_without_tag` become a filter stage placed **with** the admission filters and before affinity scoring, publishing a dropped map through `_log_and_raise_on_error()` like every other filter, with its own stage name so a refusal says which constraint ejected the node. **Per F9 the status code is 409, not 507.** `_log_and_raise_on_error()` raises `LowResourceException` (`scheduler.py:386-392`) and the create path answers that with 507 'insufficient resources' plus a delete (`external_api/instance.py:871-876`) -- which would tell the caller the cluster is full when it is not. So raise `AffinityConstraintUnsatisfiable`, a **subclass** of `LowResourceException`, and add an `except` clause for it to the create path *before* the existing one; `except` clauses match in order, so that ordering is the whole mechanism. Answer 409 naming the constraint and the stage, and add the 409 to that endpoint's `swagger_helper` response list. Keep the delete. Leave preflight untouched: it catches `LowResourceException` and redirects to other nodes (`node_inst_netdesc_op.py:159-180`), which is the right behaviour for a constraint another node may satisfy, and the subclass keeps that working unchanged. The filter matches co-located **instance** tags, within the requesting namespace only, exactly as the scorer already does (`scheduler.py:574-580`) -- see the Design section for why the namespace scope is inherited rather than crossed, and note that this makes `require_without_tag` a within-namespace constraint and not an isolation primitive. `prefer_with_tag` / `prefer_without_tag` contribute +1 / -1 per matching co-located instance into the existing scoring loop (`:529-600`), which already has the per-candidate `affinity_detail` shape the events publish -- extend it rather than replacing it, because step 1's assertions read it. Unit tests in `shakenfist/tests/test_scheduler.py` beside the existing ordering cases. Commit subject: `Add binary affinity constraints to the scheduler.` | Not started |
| 5 | medium | sonnet | worktree | (shakenfist) The transition mapping, per F4. Map weighted specs mechanically at the point the scheduler reads them: positive value to `prefer_with_tag`, negative to `prefer_without_tag`, zero to nothing. Emit a deprecation event (not a log line -- this needs to reach an operator) **where the spec is accepted, not where it is consumed**, once per acceptance. Accept-time needs no durable marker at all, which is the point: the alternatives are an attribute write on the scheduling hot path or a read of the instance's own event history on that same path, and both are the kind of addition the budget check below exists to catch. It also puts the warning where the caller can act on it, at the moment they submit the deprecated form, rather than at some later reschedule. Do not make it per-process (it would reset on every daemon restart) or per-schedule (the scheduler runs this path on every create *and* every reschedule). **The emission site is not `_validate_instance_metadata` itself, and an earlier draft of this plan said it was.** That function is module level with the signature `(key, value)` (`external_api/instance.py:1384`), so it has no object to call `add_event()` on; and on the create path it runs at `:793-799`, under the comment 'Validate metadata before instance creation', which is before `Instance.new()` at `:810`. Emitting there would have produced no event at all on the create path -- the path `test_affinity` exercises and the path an operator using the weighted form almost certainly takes -- leaving a deprecation warning that silently reaches nobody, which defeats the one requirement it exists to meet. Split a predicate out of the validator instead, `_affinity_spec_is_weighted(value)` beside it so the shape test lives in one place, and emit at the three sites where an instance object is in scope: on the create path in the metadata-initialisation loop that already holds `inst` (`:834-837`), and in both metadata endpoints via `instance_from_db.add_event()` beside the existing 'set metadata key request from REST API' events (`:1377-1379` and `:1427-1429`). Confirm the change against `shakenfist/data/database_load_budget.yaml` before proposing the commit: an unbounded per-schedule event on a still-supported path is exactly the kind of addition that moves a measurement CI enforces, and the weighted form is expected to survive at least one more release. Decide and record whether `test_affinity` moves to the binary form or stays weighted: there is a real argument for staying, since finding 7 makes it the only automated proof the mapping works, and a separate binary case can be added beside it. Do not remove the weighted form; that is a later release, and the removal needs its own deprecation window. Unit tests for the mapping including the zero and mixed-sign cases. Commit subject: `Map weighted affinity onto the binary form.` | Not started |
| 6 | medium | sonnet | worktree | (shakenfist) Documentation. In `docs/`, state what soft affinity promises and -- more importantly -- what it does not: a preference is consulted when there is a choice, and a single-candidate placement is not a preference being honoured or violated. Document the four binary constraints, the weighted form's deprecation and its mapping, and the fact that `require_*` can make a create fail with no candidates where the weighted form would silently place anywhere. Include the diagnostic recipe, which is the durable output of this whole investigation: read `schedule have highest affinity` and `schedule final candidates` from the instance's events to tell "scored wrong" from "had no choice". `AGENTS.md` and `ARCHITECTURE.md` are unlikely to need touching; check rather than assume. Commit subject: `docs: say what soft affinity promises.` | Not started |
| 7 | low | sonnet | worktree | (shakenfist) Close-out. Set phase 6 to `Complete` in the master plan Execution table, confirm `docs/plans/index.md`'s arithmetic, and record in the phase status notes that #3565 closed on a test change rather than a scheduler change, with F2's one-line reason so a later reader does not reopen it looking for the missing fix. Commit subject: `scheduler: close out phase 6.` | Not started |

## Risks and mitigations

**F2 reads as closing a bug by changing the test.** It is the
shape of an excuse, and a reviewer should push on it. The
defence is finding 3 and only finding 3: a traced run where the
candidate set was one node, the anti-affinity score was computed
correctly, and the placement happened anyway because there was
nowhere else. If that trace is wrong the decision falls. It is
linked from the issue and the payloads are in the comment, so
this is checkable rather than assertable. Mitigated further by
F3, which makes the rewritten test *stricter* than a
flake-suppressing change would be -- it refuses to report a pass
it cannot support.

**The rewritten test could pass on a cluster where affinity is
broken.** If the assertions only check that the affine node was
in the winning tier, a scheduler that scored everything equally
would pass. Mitigated by asserting the inst3 case as well --
inst1's node must be *outside* the winning tier for a request
that scored it negative -- which a degenerate scorer fails.
Checked in step 1 by mutation: make the scorer ignore negative
contributions and confirm the inst3 assertion, and only that one,
fails.

**A hard `require` becomes a new way to make creates fail.** A
`require_with_tag` for which no candidate node hosts a matching
instance ejects every candidate. That is what it is for, but it
converts a class of silent mis-placement into visible refusal,
and an operator who adopts it casually will see creates fail
that used to succeed. Mitigated by
step 6 documenting the difference explicitly and by `require_*`
being opt-in -- no existing spec maps onto it, since the
weighted mapping produces only `prefer_*` forms.

**The weighted form outlives its deprecation.** Nothing forces
its removal, and finding 7 gives a standing reason to keep it
(the CI suite uses it). Mitigated by step 5 recording the removal
as needing its own release and its own window, rather than
implying this phase's mapping is the whole migration. Accepted:
one more release of a deprecated form is cheap.

**CI cannot verify step 1 on a pull request.** The `(collection)`
matrix is skipped on `pull_request`. Mitigated by the step's
brief requiring the rewritten test to be driven against sfcbr
first, which is the same rule and the same reason as phase 4b
step 4.

## Definition of done

- [ ] No assertion in `test_affinity` compares `inst2['node']`
      or `inst3['node']` to anything. Checked with
      `grep -n "inst[23]\['node'\]" cluster_ci_tests/test_scheduler.py`,
      which today returns lines 125 and 129 and must return
      nothing inside an assertion afterwards. Stated this way
      rather than as a ban on `['node']` outright, because step 1
      needs `inst1['node']` as the *lookup value* it checks
      against the winning affinity tier -- that is not a
      placement assertion, and a blanket grep would either fail a
      correct implementation or push step 1 into deriving inst1's
      node from events to satisfy a check. F2's guarantee is
      about inst2 and inst3. (Run at planning time: the more
      obvious `inst\['node'\]` matches nothing at all, because
      the assertions name `inst1`, `inst2` and `inst3`.)
- [ ] `test_affinity` skips, with a message naming the candidate
      count, when the *unforced* pass's `schedule have highest
      affinity` event holds fewer than two `affinity_detail`
      entries. Two wrong sources are ruled out and neither may
      reappear: `schedule final candidates` (post-narrowing), and
      any affinity event matched by message alone (the forced
      `find_candidates()` calls each publish one with a single
      entry).
- [ ] **`test_affinity` does not skip on a healthy three-node
      run.** This is the check that catches the whole family of
      gate mistakes above, both of which were caught in review
      rather than by a criterion, and it is falsifiable in one
      run against sfcbr.
- [ ] `test_affinity` fails, rather than skipping, when the
      unforced `schedule inputs` / `schedule have highest
      affinity` pair cannot be found -- a missing event means
      the read was wrong, not that the run was degenerate.
- [ ] Mutating the scorer to ignore negative affinity
      contributions fails the inst3 assertion and no other test.
- [ ] A create with `require_with_tag` naming a tag no
      *co-located instance* carries fails with **409**, naming
      the constraint and the stage -- not 507, not a traceback
      and not a silent placement. 507 is the specific wrong
      answer here (F9): it tells the caller the cluster is full
      when it is not, and an earlier phrasing of this bullet
      ("a no-candidate refusal naming that stage") was satisfied
      by it. (Instance tags, not node properties -- Shaken Fist
      has no node capability tags.)
- [ ] A create carrying a **weighted** affinity spec emits the
      deprecation event, checked by reading that instance's own
      events after a create. Stated against the create path
      deliberately: the two metadata endpoints are the easy half,
      and an implementation that covers only them warns nobody
      who used the form the way `test_affinity` does.
- [ ] `test_affinity`'s event read passes `event_type='audit'`
      and `limit=1000` explicitly, and pairs the unforced
      scheduling events by `request_id`.
- [ ] `docs/` says in as many words that `require_without_tag`
      is scoped to the requesting namespace and is not an
      isolation primitive.
- [ ] A weighted spec and its mapped binary equivalent produce
      the same candidate ordering **for specs where every weight
      shares a sign and each tag is requested alone**, asserted
      by a unit test that builds both and compares. Mixed
      magnitudes are *expected to diverge* and this is asserted
      too, not left as an unstated exception: `{'a': 100, 'b': 1}`
      maps to `prefer_with_tag: ['a', 'b']`, so a node carrying
      only `b` and a node carrying only `a` tie at +1 where the
      weighted form ranked them 1 against 100. F4 discards the
      magnitude deliberately, so a criterion demanding identical
      ordering in every case would be a gate step 5 cannot pass,
      and the only way to pass it would be to abandon F4.
- [ ] #3565 is closed with the F2 reasoning recorded on it, and
      the forced-candidate retry defect exists as its own issue,
      referenced from #3565.
- [ ] No document still tells phase 6 to rule out the
      lost-spreading mechanism: the master plan stub and D6 are
      both corrected by the planning commit.
- [ ] `docs/` states that a single-candidate placement is neither
      a preference honoured nor violated.
- [ ] An affinity dictionary **value** which is a list, a dict
      or `None` is refused with a 400 from instance create and
      from both metadata endpoints -- that is, `{'affinity':
      {'first-node': ['a']}}`, not `{'affinity': ['a']}`. Today
      all three return 500. Stated at that level deliberately:
      the outer cases are already handled, since an `affinity`
      value which is a list is refused by `isinstance(value,
      dict)` (`:1398-1400`) and one which is `None` by
      `if not value` (`:1387-1388`). Only the inner coercion
      leaks a `TypeError`.
- [ ] `pre-commit run --all-files` passes.

## Future work

- **Removing the weighted affinity form.** Needs its own
  release and deprecation window; see F4 and the risk above.
- **The placement retry behaviour of finding 6.** Filed by
  step 2 per F8, once step 2 has pinned it to a call site.
- **Whether an activity metric belongs in the ranking at all.**
  Phase 00a's surviving observation: `cpu_load_1` measures
  activity, not occupancy, so a node packed with idle instances
  ranks ahead of a busier node with more room. Now that the
  capacity counters supply an occupancy measure the ranking could
  use instead, this is answerable -- but it is a ranking-model
  change on top of a ranking-model change, and doing both at once
  would make neither reviewable. Deliberately left.
- **Guaranteed co-location**, if a workload ever needs it. F7
  names the honest mechanism: a hard `require_with_tag` plus
  enough capacity, never a soft preference with leverage.

## Back brief

Two gates, both cheap to agree and expensive to redo.

**Before step 1 is written**, agree F2. It closes a scheduler bug
report without changing the scheduler, and if that is wrong the
whole first half of this phase is wrong. The argument is one
traced run; read it before agreeing.

**Before step 4 is written**, agree the Design section's value
shape -- the four reserved keys under the existing `affinity`
metadata key, distinguished from the weighted form by value type.
It is an API surface, so it is permanent from the moment it
ships, and it is cheap to change now and tedious once a
validator, a mapping, tests and documentation all reference it.
