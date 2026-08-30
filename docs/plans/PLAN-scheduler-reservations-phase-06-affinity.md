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
`scheduler.py:611-631` for the ranking precedence PR 3722
landed, and `scheduler.py:260` for the hard ceiling. The current
locations are `:489-502`, `:637-658` and `:187-256`. The claims
those references support are still true -- admission does run
before affinity scoring, and `hard_max_cpus` is still absolute
-- but anyone checking D6 against the tree will bounce off the
line numbers first.

Two of those three were got wrong in an earlier draft of this
finding, which is worth recording in a finding that exists to
complain about stale references. `:487` is the tail of the
preceding `cpu_max_per_instance` call, not the start of the
idle-CPU block; and `:611-631` is D6's citation for the
*ranking precedence* -- the load-shed-after-affinity ordering,
now the `narrowed` block at `:637-658` -- not for affinity
scoring, which is a different block at `:529-600`. The second
mistake is the instructive one: the range had drifted *and* the
thing it pointed at was not what the replacement named, so
re-deriving a line number from what the citing text says, rather
than from the nearest plausible block, is the actual check.

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

- The master plan's phase 6 stub gains the traced mechanism and
  a dated note marking the 2026-08-19/08-22 correction's
  instruction to rule out the lost-spreading mechanism as
  **discharged**. The superseded instruction is *kept*, not
  deleted, for the same reason D6's three positions are kept: a
  plan document should show what was believed at the time. An
  earlier draft of this bullet said the stub "loses" the
  instruction, and the definition-of-done bullet said no
  document still carries it -- neither described the tree, and a
  criterion that is already false at merge cannot be used as a
  check later.
- D6's stage note is added: a dated note records that the stage
  which binds is now memory rather than CPU. Two of D6's line
  references are updated with it -- the CPU admission filter and
  the hard ceiling -- and, per finding 2, so is the `:611-631`
  citation for PR 3722's ranking precedence.

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
does not wait behind an API surface and a back-brief gate. Gated
by neither is meant literally, so step 3 files its own issue and
closes it: routing that through step 2's triage pass would have
made a live public-API 500 wait on step 1 merging, which is a
cluster-CI rewrite that has to be driven against sfcbr by hand
first. An earlier draft did exactly that while this paragraph
claimed otherwise.

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

*A third wrong source sits inside the event that fixes the other
two.* `schedule have highest affinity` publishes `'candidates':
preferred` (`scheduler.py:604,609`), and `preferred` is the
**winning affinity tier**, not the input candidate set. The key
name is the whole trap: `schedule final candidates` and
`schedule forced candidates` use `candidates` to mean a candidate
list, so an implementer who has been sent to the right event and
needs a count will find the wrong number sitting under the
obvious key. On a healthy cluster inst2's winning tier is the
single node carrying the tag, so `len(extra['candidates'])` is 1
and the test skips forever -- the identical failure, now inside
the chosen event. **The count is `len(affinity_detail)`, and
never `len(extra['candidates'])`.**

*The same key is the right source for a different question, and
this plan owes it the same naming.* The two tier assertions ask
whether inst1's node is **in** the winning tier, and the winning
tier is exactly what `extra['candidates']` holds. So read tier
membership from it, and the count from `affinity_detail`. Saying
only "never `extra['candidates']`" three times over would push an
implementer into rebuilding the tier from `by_affinity` or from
the `affinity_detail` scores -- more code, and code that can
disagree with the scheduler's own `sorted(by_affinity,
reverse=True)[0]` choice, which is the disagreement a test read
from the scheduler's events exists to avoid.

**A second skip condition, for a different degeneracy.** Count is
not the only way a run can carry no information. Finding 5's
mechanism is an admission filter ejecting the *affinity target
specifically*: in the traced run inst1's node survived
`sufficient_idle_cpu` and was dropped at
`sufficient_idle_memory`. If that happens while two or three
other candidates remain, the count guard does not fire, and step
1's inst2 assertion fails -- because inst1's node is not in
`affinity_detail` at all. The rewritten test would then go on
failing for exactly the reason F2 holds is not a scheduler defect
and F7 declines to soften, and the failure would read as an
affinity bug: the precise confusion this phase exists to end.
F2's own wording already allows for it ("or legitimately had no
choice"); it has to become a check rather than a clause.

*This skip is all-or-nothing across both assertions, and that is
accepted.* Because the two assertions live in one test method,
inst3's anti-affinity coverage is lost on every run where the
skip fires -- which is every `slim-tier` run until
`PLAN-ci-cloud-sizing` lands. The tempting fix is to let inst3's
assertion run anyway, on the grounds that if inst1's node was
never a candidate then inst3 provably was not placed there. That
is exactly why it must not run: an assertion that cannot fail is
not coverage, and a green from a trivially-true assertion is the
same false pass finding 3 describes, one assertion over.
Splitting `test_affinity` into two methods would recover the
coverage honestly, but each method needs the three-instance
setup, and doubling a cluster-CI instance create is a real cost
against a runner fleet that is already the binding constraint on
merge throughput. So: accept the loss, and make it visible
rather than silent -- which is what step 6's requirement to
record the expected skip per topology is for.

**And "the unforced pass" is two passes, not one.** inst2 and
inst3 are separate creates, scheduled by separate
`find_candidates()` calls at different moments against candidate
sets that need not match -- and the traced #3565 run is precisely
a case where one had collapsed and the other had not. So both
skip conditions are evaluated **per instance**, against inst2's
unforced pass for the inst2 assertion and inst3's for the inst3
assertion, and the test skips if *either* pass is degenerate. The
singular reading is the dangerous one: guards computed from
inst2's events alone leave the inst3 assertion running against a
pass that may have had one candidate, or may have had inst1's
node already ejected, and it then fails for exactly the mechanism
F2 holds is not a scheduler defect. That is the same failure this
whole finding exists to prevent, reached through the one door
left open by reading a singular noun.

So the test also skips when inst1's node is absent from that
instance's unforced pass's `affinity_detail` keys, with a message
that tells the two skips apart in CI output -- `affine node not a
candidate` against `only N candidates`. Expect this one to fire on
`slim-tier` until `PLAN-ci-cloud-sizing` lands, and not to fire
on `slim-primary`; a test that skips permanently on one topology
reads as green, so step 6 records which skip is expected where.

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

*One consequence to write down, because `prefer_without_tag`
does not read like what it is.* The score is a **sum**, over
neighbours and over tags alike, so an avoid match can be outvoted
by neighbour count on the other axis. Ask for `prefer_with_tag:
['a']` and `prefer_without_tag: ['b']` together: a node hosting
three `a` instances and one `b` scores +2 and beats a node
hosting one `a` and no `b` at +1. That follows directly from
count-proportional scoring and is intended -- but an operator
reads `prefer_without_tag` as a soft veto, not as one term in a
sum, so step 6 documents it rather than leaving them to discover
it from a placement.

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
(`external_api/instance.py:872-877`, the
`LowResourceException` handler). Left alone, a
`require_with_tag` naming a tag no co-located instance carries
would tell the caller the cluster is full -- which is false, and
which is precisely the complaint F2 makes against D6's position
1, where a 507 is "the same ejection with a different traceback".
The definition-of-done bullet, phrased as "a no-candidate refusal
naming that stage", would have been satisfied by that wrong
answer, so it is tightened below to name the code.

**The helper has to grow a parameter for this to be possible at
all.** `_log_and_raise_on_error()` raises
`exceptions.LowResourceException` unconditionally
(`scheduler.py:391`) with a message it builds itself, so
"publish the dropped map through the helper like every other
filter" and "raise `AffinityConstraintUnsatisfiable`" cannot
both be true of the code as it stands. Give it an optional
`exception_class=exceptions.LowResourceException` argument,
defaulted so no existing call site changes, and have the require
stage pass the subclass. The message is built in the helper too,
so if the 409 body is to name the constraint and not just the
stage, the constraint detail has to reach the helper as well --
either as another optional argument or by having the stage raise
directly and use the helper only for the event. Decide that in
step 4 and say which, rather than discovering it while writing
the response body.

So the stage raises `AffinityConstraintUnsatisfiable`, **a
subclass of `LowResourceException` defined beside it in
`shakenfist/exceptions.py`** -- named because every other new
symbol in this plan is pinned to a file, and because
`node_inst_netdesc_op.py` has to import it, so defining it in
`scheduler.py` would make an operation module import the
scheduler for an exception type. The create path gains
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

**There are three capacity-shaped aborts on that path, not two.**
Between the two step 4 already names sits
`AbortInstanceStart(self, 'Requested node lacks resources')`
(`node_inst_netdesc_op.py:169-171`), reached when
`inst.requested_placement` is set and the forced call at `:156`
raised -- so under step 4 an operator who pinned a node is told
it lacks resources when what it lacks is a matching tag. Same
fault, third door.

**And they are not one change, because two of the three cannot
see the exception.** An earlier draft of this decision said "the
exception is already bound by the `except` clause at `:159`, so
all three are the same one-line change". That is wrong, and
wrong in a way that would have compiled. The
`except LowResourceException as e:` suite is `:159-162` only;
`:164` onwards is dedented back to the level of the `try`, so
`:166-167` and `:169-171` are outside it -- and Python deletes
the `as` target when the suite exits (PEP 3110), so `e` is
unbound there. Checked at the interpreter: reading it raises
`NameError: cannot access local variable 'e' where it is not
associated with a value`. Only `:276` is genuinely inside an
`except` suite and can test the exception directly.

**Which is an instruction, not just an observation.** An
earlier draft said `:276` "is a different matter" and left it
there, so the site with the *easiest* fix was the one
with no fix specified. Inside that suite, branch on
`isinstance(e, AffinityConstraintUnsatisfiable)` and raise
`AbortInstanceStart(self, 'No node satisfies the requested
affinity constraints: %s' % e)` in place of `'Unable to find
suitable node'`, with a matching audit event. All three
sites, not two and an aside.

So the mechanism is a **carried flag**, not a rebound exception:
inside the suite at `:159`, set
`affinity_failure = isinstance(e, AffinityConstraintUnsatisfiable)`,
and test that local at `:166` and `:170`. This is the plan's own
house rule about guards sitting where the exception is raised,
and it is worth spelling out because the failure mode is
narrow: the reschedule path runs only under cluster CI in the
merge queue (`docs/developer_guide/coding_rules.md:341-352`), so
a `NameError` written here would pass the PR and land in the
queue.

**Initialise that flag before the `try`, not only inside the
suite.** As the code stands `:157` returns on success, so `:166`
is reachable only by way of the except suite and an assignment
made solely inside it would in fact always have run. That is a
property of today's control flow, not of the guards that read
the flag: any later edit adding a non-returning path through the
`try` reintroduces exactly the unbound-local failure this
finding exists to describe, on the one path a pull request does
not exercise. `affinity_failure = False` immediately before the
`try` costs a line and removes the dependency.

**The three aborts also need a test, because nothing else here
will catch them.** The rest of this phase's scheduler work is
testable at the unit level, but the abort path runs in the queue
daemon under cluster CI, so a mistake in it passes the pull
request and fails in the merge queue -- the same asymmetry that
makes the `NameError` above worth this much text. Step 4
therefore owes a unit test over the abort path exercising the
flag at both `:166` and `:170`, and an API-level test that the
create path answers **409 and not 507**: the `except` ordering
is called the entire mechanism above, and a scheduler-level test
asserting the exception *type* cannot observe which clause
caught it.

## Design: where the binary form lives

Affinity is not a first-class instance field. It is instance
metadata under the reserved key `affinity`
(`instance.py:247`, the `affinity` property at `:739`), validated at
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
(`instance.py:786-787`, the `tags` property, consumed at
`scheduler.py:568-587`) -- and not a property of the node.
Shaken Fist has no node capability tags, so an example like
`require_with_tag: ['ssd']` would teach a model that does not
exist. This is why
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
decisions and can land ahead of the model. Step 3 also files the
issue it closes, rather than step 2, so that nothing about this
fix waits on the test rewrite; it is a live 500 on a public API
and should be tracked whether or not this phase lands.

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

Five of the seven briefs live in `### Step N brief` sections below
this table rather than inside their cells, which is a deliberate
deviation from `PLAN-TEMPLATE.md` and is recorded here so it is not
read as an oversight. The template puts the brief in the cell, and
that works at the ~300 character briefs the sibling phase plans in
this series carry. Steps 1 and 4 here reached 8,700 and 11,100
characters, twenty times the house norm, at which point the row is a
single unwrapped line in the source and one unreadable cell in
mkdocs -- so the container was defeating the content it exists to
carry. The cells keep a real one-or-two-sentence brief and a link;
nothing was shortened in the move, and the sections carry the text
verbatim. The `### Step N` heading level is already used by sibling
plans for their post-execution implementation notes, so this reuses
a shape a reader of these plans knows rather than inventing one.

| Step | Effort | Model | Isolation | Brief for sub-agent | Status |
|------|--------|-------|-----------|---------------------|--------|
| 1 | high | opus | worktree | Rewrite `test_affinity` onto the scheduler's audit events per F2 and F3, asserting affinity tier membership rather than final co-location, and skipping (not passing) on a degenerate run. **Full brief: [Step 1](#step-1-brief-rewrite-test_affinity-onto-the-events).** | Not started |
| 2 | low | sonnet | none | (GitHub) The #3565 disposition, once step 1 has merged. Comment on #3565 recording F2: the traced 2026-08-26 run shows a single-candidate set, so affinity was never consulted, and both halves of the assertion had the same cause; the issue is closed by candidate fix 2 rather than by a scheduler change; D6's three positions are disposed of by F2 and F7. Close it. Then deal with the retry behaviour from finding 6, and **pin it to a call site before filing** (F8). Re-read that comment's event payloads against the three sites finding 6 names: the create path's demand-waiving second walk (`external_api/instance.py:924-940`), the preflight redirect that rebuilds the candidate list excluding the current node (`operations/node_inst_netdesc_op.py:172-180`), and preflight's opening forced call against `config.NODE_UUID` (`:156`), which runs on the node the create path has just chosen. File the issue against whichever the trace matches, quoting the payloads and naming the file and line. If none of them matches, file it as an observation that says so in as many words rather than asserting a mechanism -- a wrong issue is worse than none. Cross-reference it from #3565's closing comment. Do **not** file the validator defect here: step 3 files and closes its own issue, deliberately, so that a live public-API 500 does not wait on this step, which waits on step 1 (F1). If step 3 has already landed, reference its issue from #3565's closure alongside the retry one. Also comment on `PLAN-ci-cloud-sizing`'s tracking of #3565 that its "needs a disposition in phase 0 before phase 4" is now satisfied. Include *(Triage assisted by Claude Code)*. | Not started |
| 3 | low | sonnet | worktree | The validator 500, on its own branch and its own issue: widen the `int()` coercion handler to `(TypeError, ValueError, OverflowError)` and refuse booleans. Depends on nothing else here. **Full brief: [Step 3](#step-3-brief-the-validator-500).** | Not started |
| 4 | high | opus | worktree | Build the binary affinity model: the four reserved keys, the hard `require_*` filter stage, the 409, and the three preflight abort messages. **Full brief: [Step 4](#step-4-brief-the-binary-model).** | Not started |
| 5 | medium | sonnet | worktree | Map weighted specifications onto the binary form where the scheduler reads them, and emit a deprecation event where a specification is accepted. **Full brief: [Step 5](#step-5-brief-the-transition-mapping).** | Not started |
| 6 | medium | sonnet | worktree | Documentation: rewrite `docs/user_guide/affinity.md`, put the diagnostic and discovery recipes in `docs/operator_guide/scheduler.md`, and record which skip is expected on which CI topology. **Full brief: [Step 6](#step-6-brief-documentation).** | Not started |
| 7 | low | sonnet | worktree | (shakenfist) Close-out. Set phase 6 to `Complete` in the master plan Execution table, confirm `docs/plans/index.md`'s arithmetic, and record in the phase status notes that #3565 closed on a test change rather than a scheduler change, with F2's one-line reason so a later reader does not reopen it looking for the missing fix. Commit subject: `scheduler: close out phase 6.` | Not started |

### Step 1 brief: rewrite `test_affinity` onto the events

(shakenfist) Rewrite `test_affinity` onto the audit events, per F2 and F3.
In `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_scheduler.py`,
keep the three-instance setup unchanged and replace the two placement
assertions (`:124-131`, whose `['node']` arguments are on `:125` and
`:129`) with assertions read from the scheduler's own events. The events
are already fetched by `_add_scheduler_detail()` (`:19-35`), which filters
`get_instance_events()` to messages starting with `schedule` -- factor its
fetch out into a helper returning the events so both the detail-attachment
and the assertions use one read, rather than fetching twice. Assert, for
inst2: that `schedule have highest affinity` scored inst1's node in the
winning tier. For inst3: that inst1's node was **not** in the winning tier.
Then **skip, do not pass**, when the scorer had fewer than two candidates
to choose among, with the count in the skip message (F3). Take that count
from `len(affinity_detail)` in the `schedule have highest affinity` event
**of the unforced scheduling pass**, located by finding the `schedule
inputs` event whose `forced_candidates` is false (`scheduler.py:438`) and
taking the affinity event carrying the **same `request_id`**. Pair on
`request_id`, not on adjacency: it is a field of `EventReadRow`
(`schema/event.py:84`), populated from `FLASK_REQUEST_ID`
(`eventlog.py:82-86`) and returned to clients by `row.model_dump()`
(`external_api/base.py:1479-1484`); the create path takes exactly one of
its two `find_candidates()` branches per request
(`external_api/instance.py:866-870`), so the id identifies the unforced
pass outright, while the preflight call runs in the queue daemon with no
flask request and no `request_id` at all. **Guard the join against a null
key on both sides.** `eventlog.py:84` reads the environ with a bare
`.get()`, so an absent key yields `request_id = None` -- and if the
create-path events carried `None` too, an equality match would return the
preflight events as well, and taking the first or last could select the
forced pass, whose `affinity_detail` has exactly one entry: a silent
permanent skip, the failure F3 exists to prevent, reached by a fourth
route. In practice the key is always set on the create path --
`RequestID(app)` (`external_api/app.py:63`) is WSGI middleware that does
`environ["FLASK_REQUEST_ID"] = req_id` on every request, generating a uuid4
when the client sends no `X-Request-ID` header -- which is *why* the
pairing is sound, and is worth stating because this plan had been asserting
the pairing without saying what guarantees it. (The `.get(..., 'none')`
defaults elsewhere -- `external_api/base.py:1274`, `app.py:227,262,614` --
are defensive logging, not evidence of absence.) Use the pairing only when
the unforced `schedule inputs` event's `request_id` is truthy; if it is
falsy, fall back to adjacency, and `self.fail()` rather than skip when
adjacency cannot identify a pass either. **Define adjacency exactly, rather
than leaving the word to be interpreted**: sort the instance's events
ascending by `timestamp`, find the chosen `schedule inputs` event in that
order, and take the first `schedule have highest affinity` strictly after
it; `self.fail()` if there is none. It is fragile precisely because those
timestamps are floats that can tie, which is why it is the fallback and not
the mechanism. **It is not dead code, though it is close to it.**
`RequestID(app)` sets the id on every request, so a current cluster always
pairs -- but `request_id` is a recent addition to `EventReadRow`, and this
test runs against whatever sf-api the CI cluster is running, so an older
API that does not publish the field is the reachable path. Left undefined
it would be a latent flake with no test behind it; defined, it is a
documented degradation. **Three** wrong sources are ruled out in F3 and
must not be reintroduced: `schedule final candidates` (post-narrowing,
holds one node whenever affinity works for inst2); any `schedule have
highest affinity` matched by message alone (`find_candidates()` runs
several times per create, and the forced calls each publish one with a
single entry); and -- inside the correct event -- `extra['candidates']`,
which is `preferred`, the post-scoring winning tier
(`scheduler.py:604,609`), and not the input set. That third one is the
nearest to hand and the easiest to reach for, because `candidates` means an
actual candidate list in the two sibling events. The count is
`len(affinity_detail)`, full stop. **The same key is the right source for
the other question**, and is named here so that being told three times not
to touch it does not send you rebuilding the tier by hand: the two tier
assertions ask whether inst1's node is *in* the winning tier, and
`extra['candidates']` *is* the winning tier. Tier membership from
`extra['candidates']`, count from `affinity_detail`. Rebuilding the tier
from `by_affinity` or from the `affinity_detail` scores is more code and
can disagree with the scheduler's own `sorted(by_affinity,
reverse=True)[0]` choice. The lookup itself needs no translation:
`inst1['node']` and the keys of `affinity_detail` are both node UUIDs drawn
from the same `get_active_node_metrics()` keyspace -- confirmed at planning
time, and worth stating because if it were false the test would skip
permanently and silently. **The fetch must also be widened.**
`_add_scheduler_detail()` calls `get_instance_events()` with no limit, and
that endpoint defaults to `limit=100` (`external_api/instance.py:1203`)
over rows ordered by timestamp descending (`mariadb.py:6139-6146`) -- so it
returns the *newest* hundred, while the create-path scheduling events are
the *oldest* an instance has, behind all its networking, image, boot and
agent events. Pass `event_type='audit', limit=1000` explicitly, and put
that pair in the helper's *body* rather than in its signature -- it takes
the instance uuid and nothing else. The truncation this instruction removes
was reintroducible precisely because the widening lived at the call site; a
helper that hardcodes both gives every reader the widened fetch by
construction. Both, not one: 1000 is the endpoint's hard cap (`:1193-1195`,
`{'minimum': 1, 'maximum': 1000}`), so the audit filter is what keeps a
busy create inside that ceiling rather than an optional tidiness -- and
since the brief also requires `self.fail()` when the unforced pair is
missing, a create that emits more than 1000 events would otherwise become a
test failure blamed on affinity. Two call sites in the suite already use
this idiom: `get_instance_events(inst['uuid'], event_type='mutate',
limit=1000)` (`cluster_ci_tests/test_events.py:136-137`), and
`cluster_ci_tests/test_namespace_claims.py:415-421`, whose docstring makes
exactly this argument about a default read pushing an old event off the
end. If the unforced pair is not found, `self.fail()` with that reason
rather than skipping: a missing event means the read was wrong, not that
the run was degenerate, and a skip would hide it. `affinity_detail` has two
shapes -- the normal `{'score', 'instance_count', 'considered'}`
(`:596-600`) and `{'score': 0, 'reason': 'node row not found'}`
(`:548-551`) for a candidate whose node row could not be read -- so count
its entries but do not index inside them unconditionally, or a transient
node-row failure becomes a `KeyError` and an unreadable test error instead
of a diagnosable skip -- a single-candidate run carries no information and
a green result there is the false pass finding 3 describes. **Skip on a
second condition too, per F3**: when inst1's node is not among the unforced
pass's `affinity_detail` keys, the affinity target was ejected by an
admission filter before scoring, so the run says nothing about affinity in
either direction -- and without this guard step 1's inst2 assertion fails
on precisely the mechanism F2 holds is not a scheduler defect and F7
declines to soften. Give the two skips different messages (`affine node not
a candidate` against `only N candidates`) so CI output separates them; a
single message would merge the degenerate case this phase accepts with the
one it declines to fix. Read the 2026-08-26 comment on issue #3565 first:
it contains the exact event payloads from a failing run, including an
`affinity_detail` that scored a node `-100` and placed there anyway, and it
is the specification for what these assertions must distinguish. Do not
assert final co-location anywhere -- that is the guarantee F2 establishes
the product does not make. This runs only on `merge_group`
(`docs/developer_guide/coding_rules.md:341-352`), so exercise it against
sfcbr before proposing the commit. Commit subject: `tests: assert what soft
affinity actually promises.`

### Step 3 brief: the validator 500

(shakenfist) The validator 500, on its own. Widen
`_validate_instance_metadata`'s `except ValueError` to `except (TypeError,
ValueError, OverflowError)` (`external_api/instance.py:1403-1406`). `int()`
raises `TypeError`, not `ValueError`, for a list, a dict or `None`, so an
affinity dictionary *value* of that shape escapes the handler and returns
**500** today from instance create (`:797`) and from both metadata
endpoints (`:1375`, `:1425`). **`OverflowError` is a fourth case and is not
theoretical**: `int(float('inf'))` raises it, and flask hands a bare
`Infinity` literal straight through because `req.get_json(force=True,
silent=True)` (`external_api/base.py:129`) uses `json.loads` defaults,
which accept `Infinity` and `NaN` as JSON. So `{'affinity': {'a':
Infinity}}` 500s exactly like the other three, and a two-exception fix
leaves that hole open behind a ticked box. This trap is already known in
this codebase and handled one file over -- `external_api/base.py:308-320`
refuses non-finite durations with a comment saying in as many words that
json.loads hands the bare literals through. (`NaN` needs nothing:
`int(float('nan'))` raises `ValueError`, which is already caught.) **Refuse
booleans as well**, mirroring `external_api/base.py:299-301`:
`isinstance(True, int)` is true in Python so `int(True)` is 1, and
`{'affinity': {'a': true}}` is accepted today as weight 1 -- which under
F4's mapping would silently become `prefer_with_tag: ['a']`. Record that
half honestly in the issue, the commit message **and the release notes**,
because it is the one part of this step that **changes a request that
succeeds today into a 400**, and the validator is shared, so the break has
three entry points -- instance create and both metadata endpoints; it is
worth doing here rather than later precisely because F4 is about to give
`true` a meaning nobody asked for. Do *not* replace the coercion with a
shape test (`isinstance(dv, int) and not isinstance(dv, bool)`), tempting
as it looks: `int('3')` succeeds today, so a shape test would also refuse
string-encoded integers, and this step is a bugfix for a 500, not a
tightening of what the API accepts. Get the level right too: a malformed
*outer* `affinity` value is already refused correctly -- a list by
`isinstance(value, dict)` (`:1398-1400`) and `None` by `if not value`
(`:1387-1388`) -- and only the inner per-tag coercion leaks. Unit tests in
`shakenfist/tests/` for the list-valued, dict-valued, `None`-valued and
`Infinity`-valued inner cases, which are the four that 500 now, plus the
boolean case which is the one that changes answer. This step is
deliberately separate from the binary model below and depends on none of
this phase's decisions: it is a live 500 on a public API, the fix is one
`except` clause, and it should not wait behind an API surface, a new
scheduler stage and a back-brief gate. **File its own issue and close it
with the fix** (`Fixes #NNNN`), rather than waiting for step 2's triage
pass -- step 2 waits on step 1, which is a cluster-CI rewrite that has to
be driven against sfcbr by hand, and a live 500 on a public API should not
sit behind that. Be precise about the level in the issue as well as in the
code: a malformed *outer* `affinity` value is already refused correctly,
and only the inner per-tag coercion leaks. This step waits for nothing and
can go first. Commit subject: `Refuse malformed affinity values with a
400.`

### Step 4 brief: the binary model

(shakenfist) The binary model. Per F4 and the Design section: accept the
new value shape under the `affinity` metadata key, validate it at
`external_api/instance.py:1395-1406` alongside the weighted form (four
reserved keys, list-of-string values, 400 on anything else), and consume it
in `scheduler.py`. Step 3 has already widened the `except` clause in that
same function; build the new-shape validation beside it rather than
repeating it, and **place it above the per-value coercion loop, not
below**. The binary form's values are *lists*, which is exactly the shape
step 3 has just taught that loop to refuse with a 400, so a branch added
underneath it ships a validator that rejects the whole new API surface -- a
step breaking the feature the next step adds, which no test written inside
either step would catch. Discriminate on the keys: a dictionary any of
whose keys are one of the four reserved names is the binary form, so
validate that branch and `return`, and let only the weighted branch reach
`int()`. A dictionary mixing the two shapes is refused rather than guessed
at, because either way of resolving it silently discards half of what the
caller asked for. `require_with_tag` / `require_without_tag` become a
filter stage placed as the **last** of the admission filters -- after
`sufficient_free_disk` and immediately before the affinity block at
`scheduler.py:529` -- and so before affinity scoring, publishing a dropped
map through `_log_and_raise_on_error()` like every other filter, with its
own stage name so a refusal says which constraint ejected the node. **That
helper cannot raise the subclass as it stands**: it raises
`exceptions.LowResourceException` unconditionally at `scheduler.py:391`
with a message it builds itself, so give it an optional
`exception_class=exceptions.LowResourceException` argument, defaulted so no
existing call site changes, and pass `AffinityConstraintUnsatisfiable` from
the require stage. The message is built in the helper too, so decide *in
this step* how the constraint detail reaches the 409 body -- a second
optional argument, or the stage raising directly and using the helper only
for the event -- and record which. Do not leave it to be discovered while
writing the response body. **The position is what keeps the `placements`
memo cheap, and it is the reason to choose it.** Matching co-located
instance tags means calling `_placed_instances()` (`scheduler.py:169-185`),
one `Node.from_db()` plus one `Instance.from_db()` per placed instance per
candidate node; today that is paid exactly once because the memo is a local
created inside the affinity block (`:541`). A separate stage cannot see
that local, so the memo has to move above it -- but a hard filter is
order-independent for correctness, only the stage name in the refusal event
changes, so there is a free choice about *how far* above. Putting the stage
among the earlier admission filters would drag the memo in front of the
CPU, RAM and disk pruning, where it reads placements for the full candidate
set: strictly more expensive than the pass that exists today, and paid on a
hot path to save nothing. Putting it last instead moves the memo by two
statements over an already-pruned set, and the filter and the scorer share
one read. Take the second. Keep one guard even so: the stage must **return
immediately when the instance requests neither `require_with_tag` nor
`require_without_tag`**, because it still runs ahead of the load-shedding
filters, and without it every create walks candidates it has no constraint
to test them against. Confirm the result against
`shakenfist/data/database_load_budget.yaml` before proposing the commit --
the same requirement step 5 carries for a far smaller addition, on the same
instance-create hot path. While you are there, take the free win the same
reading exposes: the affinity loop calls `_placed_instances()`
unconditionally today (`scheduler.py:544`) with no short-circuit on an
empty `inst.affinity`, so every create already pays a full `Node.from_db()`
plus one `Instance.from_db()` per placed instance per candidate for a
scorer that has nothing to score. Skipping the walk when there is nothing
to score *reduces* the measured load rather than holding it flat, which is
a better outcome for the budget check than a wash. **But the skip has to
establish what the code after the walk expects, or it breaks every create
that requests no affinity -- which is most of them.** The walk is what
populates `by_affinity`, and the next statement is `highest_affinity =
sorted(by_affinity, reverse=True)[0]` (`scheduler.py:603`) over a
`defaultdict(list)`: empty if the loop body never ran, so `sorted(...)[0]`
raises `IndexError` and `preferred` never reaches the unconditional
load-shed block at `:637-658`. Seed `by_affinity[0] = list(candidates)` on
the skipped path, so everything downstream sees what it saw before -- one
tier, every candidate, score zero. Skip on whether there are any tags to
*score* (the `prefer_*` lists, after mapping) rather than on
`inst.affinity` being empty, or a specification of nothing but `require_*`
constraints walks anyway. **Which makes step 5 a hard prerequisite of the
short-circuit, and the two must not be separated.** The `prefer_*` lists of
a weighted specification are empty until step 5's mapping populates them,
so a short-circuit landing in step 4 alone would skip the walk for *every
existing caller* -- silently, since no create fails and `test_affinity`
would merely skip green in cluster CI. Land the short-circuit in step 5
beside the mapping, not here; step 4 leaves the walk unconditional. If for
some reason they must be separated, the predicate has to be "no `prefer_*`
tags **and** not a weighted specification" instead, which is worse code
written to survive an ordering nothing needs. **Say what the event carries
there too.** `schedule have highest affinity` is still published on the
skipped path, because F3 makes it the record step 1 reads and step 6 makes
it the recipe an operator reads; its `candidates`, `highest_affinity` and
`by_affinity` are all populated, and its `affinity_detail` is `{}` with no
`instance_count` anywhere. That shape is the walk correctly declining to
run, not events going missing, and step 6's recipe has to cover it -- a
no-affinity create is exactly the create an operator diagnosing an
unexpected placement will look at first. **Per F9 the status code is 409,
not 507.** `_log_and_raise_on_error()` raises `LowResourceException`
(`scheduler.py:386-392`) and the create path answers that with 507
'insufficient resources' plus a delete (`external_api/instance.py:872-877`,
the `LowResourceException` handler) -- which would tell the caller the
cluster is full when it is not. So raise `AffinityConstraintUnsatisfiable`,
a **subclass** of `LowResourceException`, and add an `except` clause for it
to the create path *before* the existing one; `except` clauses match in
order, so that ordering is the whole mechanism. Answer 409 naming the
constraint and the stage, and add the 409 to that endpoint's
`swagger_helper` response list. Keep the delete. Leave preflight's
*redirect* untouched: it catches `LowResourceException` and tries the other
nodes (`node_inst_netdesc_op.py:159-180`), which is the right behaviour for
a constraint another node may satisfy, and the subclass keeps that working
unchanged. **Its abort message is a different matter.** When no node can
satisfy the constraint -- a `require_with_tag` naming a tag nothing in the
namespace carries -- the redirect exhausts its candidates and raises
`AbortInstanceStart(self, 'Unable to find suitable node')` (`:276`), and
repeated preflight cycles then hit `AbortInstanceStart(self, 'Too many
start attempts')` (`:166-167`) after burning three placement attempts.
There is a **third** between them: `AbortInstanceStart(self, 'Requested
node lacks resources')` (`:169-171`), taken when `inst.requested_placement`
is set and the forced call at `:156` raised -- so an operator who pinned a
node is told it lacks resources when it lacks a matching tag. All three
messages are capacity-shaped, which is the same "tells the caller the
cluster is full when it is not" fault F9 exists to prevent, one path over;
the create path escapes it only because the instance is deleted before
preflight runs, so this is the restart and reschedule path. **They are not
the same fix, and the difference is a trap.** The `except
LowResourceException as e:` suite is `:159-162` only; `:164` onwards is
dedented back to the `try` level, so `:166-167` and `:169-171` sit outside
it and `e` has been deleted by then (PEP 3110) -- reading it there raises
`NameError: cannot access local variable 'e' where it is not associated
with a value`, confirmed at the interpreter. Only `:276` is inside an
`except` suite and can test the exception directly. For the other two,
capture a local inside the suite at `:159` -- `affinity_failure =
isinstance(e, AffinityConstraintUnsatisfiable)` -- and test that flag at
`:166` and `:170`. Initialise it to `False` immediately before the `try`
rather than only inside the suite: today `:157` returns on success so the
assignment always runs, but that is a property of the current control flow
and not of the guards, and the next edit which adds a non-returning path
through the `try` puts the unbound local back on the one path a pull
request does not exercise. Get this wrong and the `NameError` lands in the
merge queue rather than failing the PR, because the reschedule path runs
only under cluster CI (`docs/developer_guide/coding_rules.md:341-352`). Do
not change the redirect behaviour itself. The filter matches co-located
**instance** tags, within the requesting namespace only, exactly as the
scorer already does (`scheduler.py:574-580`) -- see the Design section for
why the namespace scope is inherited rather than crossed, and note that
this makes `require_without_tag` a within-namespace constraint and not an
isolation primitive. `prefer_with_tag` / `prefer_without_tag` contribute +1
/ -1 per matching co-located instance into the existing scoring loop
(`:529-600`), which already has the per-candidate `affinity_detail` shape
the events publish -- extend it rather than replacing it, because step 1's
assertions read it. Unit tests in `shakenfist/tests/test_scheduler.py`
beside the existing ordering cases, **plus two things that suite cannot
assert**. First, an API-level test that the create path answers **409 and
not 507**: the `except` ordering is the entire mechanism per F9, and a
scheduler test asserting the exception type passes whichever clause catches
it, so the one mistake the ordering exists to prevent is invisible to it.
Second, a unit test over the preflight abort path exercising
`affinity_failure` at both `:166` and `:170` -- that path runs only in the
queue daemon under cluster CI, so an error there passes the pull request
and lands in the merge queue. Also assert that a create requesting no
affinity at all still schedules, which is the cheapest possible guard on
the short-circuit above. Commit subject: `Add binary affinity constraints
to the scheduler.`

### Step 5 brief: the transition mapping

(shakenfist) The transition mapping, per F4. Map weighted specs
mechanically at the point the scheduler reads them: positive value to
`prefer_with_tag`, negative to `prefer_without_tag`, zero to nothing. Emit
a deprecation event (not a log line -- this needs to reach an operator)
**where the spec is accepted, not where it is consumed**, once per
acceptance. Accept-time needs no durable marker at all, which is the point:
the alternatives are an attribute write on the scheduling hot path or a
read of the instance's own event history on that same path, and both are
the kind of addition the budget check below exists to catch. It also puts
the warning where the caller can act on it, at the moment they submit the
deprecated form, rather than at some later reschedule. Do not make it
per-process (it would reset on every daemon restart) or per-schedule (the
scheduler runs this path on every create *and* every reschedule). **The
emission site is not `_validate_instance_metadata` itself, and an earlier
draft of this plan said it was.** That function is module level with the
signature `(key, value)` (`external_api/instance.py:1384`), so it has no
object to call `add_event()` on; and on the create path it runs at
`:792-799`, under the comment 'Validate metadata before instance creation',
which is before `Instance.new()` at `:810`. Emitting there would have
produced no event at all on the create path -- the path `test_affinity`
exercises and the path an operator using the weighted form almost certainly
takes -- leaving a deprecation warning that silently reaches nobody, which
defeats the one requirement it exists to meet. Split a predicate out of the
validator instead, `_affinity_spec_is_weighted(value)` beside it so the
shape test lives in one place, and emit at the three sites where an
instance object is in scope: on the create path in the
metadata-initialisation loop that already holds `inst` (`:835-838`), and in
both metadata endpoints via `instance_from_db.add_event()` beside the
existing 'set metadata key request from REST API' events (`:1378-1380` and
`:1428-1430`). Record the limit this choice accepts, in the step's commit
message and in step 6's documentation: accept-time covers **new acceptances
only**, so every instance already carrying a weighted spec when this lands
warns nobody, ever. That is tolerable because the event is not the
migration mechanism -- but F4 commits to removing the weighted form in a
later release, and an operator will need to find those instances then, so
step 6 owes them a way to. Confirm the change against
`shakenfist/data/database_load_budget.yaml` before proposing the commit: an
unbounded per-schedule event on a still-supported path is exactly the kind
of addition that moves a measurement CI enforces, and the weighted form is
expected to survive at least one more release. Decide and record whether
`test_affinity` moves to the binary form or stays weighted: there is a real
argument for staying, since finding 7 makes it the only automated proof the
mapping works, and a separate binary case can be added beside it. **Add
that separate case rather than leaving it optional.** As the rest of this
plan stands, all four constraints could ship with unit coverage only,
against a project standard that prefers functional tests to unit tests
where only one is possible (`CLAUDE.md`). Add a cluster-CI method in
`cluster_ci_tests/test_scheduler.py` reusing step 1's event helper: a
`prefer_with_tag` create asserted through the same tier check, and a
`require_with_tag` create naming a tag nothing carries, asserted to fail
with a 409. The second needs no successful create at all and is therefore
cheap -- and it is the one case a unit test with mocked placements
exercises least convincingly, since the bootstrapping dead end and the
namespace scope are both properties of a real cluster's instance
population. Drive it against sfcbr before proposing the commit, per the
merge-queue-only rule. Do not remove the weighted form; that is a later
release, and the removal needs its own deprecation window. Unit tests for
the mapping including the zero and mixed-sign cases. Commit subject: `Map
weighted affinity onto the binary form.`

### Step 6 brief: documentation

(shakenfist) Documentation. **The page to rewrite is
`docs/user_guide/affinity.md`** (registered in the nav at
`mkdocs.yml:454`), and the diagnostic material goes in
`docs/operator_guide/scheduler.md`. Naming them matters because affinity.md
as it stands **contradicts F2 in an admonition**: it says Shaken Fist
"filters possible candidate hypervisors based on the affinity coefficients
specified", which is the exact over-strong reading F2 identifies as the
root of #3565, and it teaches the weighted form as the only form with a
-100..100 recommendation. A step told only "in `docs/`" can satisfy itself
by adding a new page and leave that one teaching the deprecated form and
the wrong guarantee, which is worse than before: two pages disagreeing.
Rewrite it. Note the claim is not simply false any more -- `require_*`
really does filter -- so the fix is to say which half filters and which
half ranks, not to delete the sentence. In it, state what soft affinity
promises and -- more importantly -- what it does not: a preference is
consulted when there is a choice, and a single-candidate placement is not a
preference being honoured or violated. Document the four binary constraints
**in one table**, with the hard/soft distinction and the namespace scope in
that same view rather than as prose paragraphs elsewhere on the page. The
four names share a `_with_tag` / `_without_tag` suffix family and carry two
separate counterintuitive readings -- `prefer_without_tag` is a term in a
sum and not a soft veto, and `require_without_tag` is scoped to the
requesting namespace and is therefore not an isolation primitive -- so an
operator who reads about one name must see both caveats without hunting.
Document the weighted form's deprecation and its mapping, and the fact that
`require_*` can make a create fail with no candidates where the weighted
form would silently place anywhere. **Document the bootstrapping dead end
explicitly**, because it is the first thing an operator hits and it reads
as a bug: the constraints match tags on instances *already placed* on a
candidate node, so the first instance of a group requesting
`require_with_tag: ['web']` ejects every candidate and gets a 409, and
stays that way for as long as nothing in the namespace carries the tag --
including any instance created under the same constraint. State the
workaround in the same breath: create the seed instance carrying the tag
and *without* the `require_*` constraint, or use `prefer_with_tag`, which
degrades to a ranking that nothing to rank leaves alone. Document that
`prefer_*` terms **sum**, across neighbours and across tags, with the
worked two-tag example from F4: with `prefer_with_tag: ['a']` and
`prefer_without_tag: ['b']` both requested, a node hosting three `a`
instances and one `b` scores +2 and beats a node hosting one `a` and no `b`
at +1. `prefer_without_tag` reads as a soft veto and is not one; it is a
term in a sum, and an operator who learns that from a placement rather than
from the documentation will read it as a bug. Include the diagnostic
recipe, which is the durable output of this whole investigation: read
`schedule have highest affinity` and `schedule final candidates` from the
instance's events to tell "scored wrong" from "had no choice". **Cover both
shapes of that event**, per step 4: a create which requested affinity
carries a populated `affinity_detail` with a per-candidate `score`,
`instance_count` and `considered` breakdown, while a create which requested
none carries `affinity_detail: {}` with `candidates`, `highest_affinity: 0`
and `by_affinity` still populated. The second is the scorer correctly
declining to run and not a diagnostic gap, and it is the shape an operator
meets most often, so a recipe written only against the first sends them
looking for events that were never going to exist. Include a **discovery
recipe** for existing weighted specs. Specify it as a **client-side loop
over `instance list` calling `instance show` per instance**, and accept
that cost: there is no bulk or filtered metadata read behind it.
`mariadb.get_object_metadata()` is per-object, and no `sf-ctl` subcommand
exposes a query. So the honest options are an N+1 loop now or a new
server-side capability, and a new capability is not something a
medium-effort documentation step should be discovering halfway through --
it would be its own step, and this phase does not need it. The loop is a
one-off run before an upgrade, not a monitoring query, so N+1 is the right
trade here; say so in the documentation rather than leaving a reader to
wonder why it is shaped that way. Two traps to write into the recipe:
`instance show` renders metadata as **Python literals** and not JSON
(`{'static-runner': -10}`, single quotes), so it parses with
`ast.literal_eval` and a `json.loads` version silently reports a clean
cluster; and the weighted/binary test is the same one the server uses, a
dictionary using none of the four reserved names. The recipe lists
instances whose `affinity` value is the weighted shape, because step 5's
deprecation event covers new acceptances only, and the removal release will
need that list. **Run the recipe once against a real cluster** and record
what it returned. It is the only thing standing between the removal release
and a silent breakage, and a recipe that has never been executed is not a
mitigation -- everything else this plan commits to is falsifiable, and this
should be too. Record which of `test_affinity`'s two skips is expected on
which CI topology (F3): `affine node not a candidate` is expected on
`slim-tier` until `PLAN-ci-cloud-sizing` lands, and neither skip is
expected on `slim-primary`, so a permanently-skipping test cannot pass for
green. `AGENTS.md` and `ARCHITECTURE.md` are unlikely to need touching;
check rather than assume. Commit subject: `docs: say what soft affinity
promises.`

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

**`require_with_tag` has a bootstrapping dead end, and it looks
exactly like a bug.** The constraint matches tags on instances
*already placed* on a candidate node, so the first member of a
group cannot be created under it: nothing in the namespace
carries the tag, every candidate is ejected, and the answer is a
409 which repeats forever -- including for an attempt to create
the very instance that would satisfy it. This is the model
working, not a defect, but it is the first thing an operator
adopting `require_with_tag` will meet and it arrives as a
refusal with no obvious way forward. Mitigated by step 6
documenting it beside the constraint table with the workaround
stated in the same place: seed the group with an instance that
carries the tag and *not* the constraint, or use
`prefer_with_tag`, where having nothing to rank is harmless.
Not mitigated by a code change: a `require` which silently
relaxes itself when it is inconvenient is not a require, and
that is the same softening F7 declines.

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
      entries. Three wrong sources are ruled out and none may
      reappear: `schedule final candidates` (post-narrowing), any
      affinity event matched by message alone (the forced
      `find_candidates()` calls each publish one with a single
      entry), and `extra['candidates']` *within the correct
      event*, which is the post-scoring winning tier and not the
      input set.
- [ ] `test_affinity` also skips, with a **different** message,
      when inst1's node is absent from the unforced pass's
      `affinity_detail` keys -- the affinity target was ejected
      by an admission filter before scoring. Falsifiable by
      grepping the two skip messages: one names a count, the
      other names the missing node, and CI output tells them
      apart. Without this the test fails on the one mechanism F2
      holds is not a scheduler defect.
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
      the same candidate ordering **for specs in which every
      weight has the same magnitude** (which includes every
      single-tag spec; positive scaling is order-preserving,
      which is why magnitude and not tag count is the real
      condition), asserted
      by a unit test that builds both and compares. Mixed
      magnitudes are *expected to diverge* and this is asserted
      too, not left as an unstated exception: `{'a': 100, 'b': 1}`
      maps to `prefer_with_tag: ['a', 'b']`, so a node carrying
      only `b` and a node carrying only `a` tie at +1 where the
      weighted form ranked them 1 against 100. F4 discards the
      magnitude deliberately, so a criterion demanding identical
      ordering in every case would be a gate step 5 cannot pass,
      and the only way to pass it would be to abandon F4.
- [ ] The discovery recipe for existing weighted specs exists in
      `docs/`, and has been **run once against a real cluster**
      with its output recorded. Step 5's deprecation event
      reaches new acceptances only, so this recipe is the whole
      mitigation for every instance already carrying a weighted
      spec when the removal release lands -- and it is the one
      load-bearing deliverable in this plan that would otherwise
      have no falsifiable criterion at all.
- [ ] #3565 is closed with the F2 reasoning recorded on it, and
      the forced-candidate retry defect exists as its own issue,
      referenced from #3565.
- [ ] No document still tells phase 6 to rule out the
      lost-spreading mechanism **without saying, at that point,
      that it is already ruled out**. The superseded instruction
      stays in the master plan stub, immediately under a dated
      note discharging it, exactly as D6's three positions stay
      under F2 and F7. Phrased this way because the earlier
      phrasing -- "no document still tells phase 6 to rule it
      out" -- was false in the same commit that asserted it, and
      a criterion false at merge time is not a criterion.
- [ ] `docs/` states that a single-candidate placement is neither
      a preference honoured nor violated.
- [ ] An affinity dictionary **value** which is a list, a dict,
      `None` or `Infinity` is refused with a 400 from instance
      create and from both metadata endpoints -- that is,
      `{'affinity': {'first-node': ['a']}}`, not `{'affinity':
      ['a']}`. Today all four return 500. `Infinity` is listed
      because it is the one that a two-exception fix misses:
      `int()` raises `OverflowError` for it, not `TypeError`, and
      flask hands the bare JSON literal through
      (`external_api/base.py:129`, and the comment at `:308-320`
      documenting the same trap). A bullet naming only the first
      three would have been signed off with that hole open.
      Stated at this level deliberately: the outer cases are
      already handled, since an `affinity` value which is a list
      is refused by `isinstance(value, dict)` (`:1398-1400`) and
      one which is `None` by `if not value` (`:1387-1388`). Only
      the inner coercion leaks.
- [ ] `{'affinity': {'first-node': true}}` is refused with a
      400. This one is a **deliberate compatibility break**, not
      a 500 being fixed: `int(True)` is 1, so it is accepted
      today, and under F4's mapping it would quietly become
      `prefer_with_tag: ['first-node']`. Recorded as a break in
      the step 3 issue and commit message. `{'affinity':
      {'first-node': '3'}}` still succeeds -- a shape test would
      have refused it too, which is why step 3 widens the
      `except` rather than replacing the coercion.
- [ ] Nothing in the preflight abort path reads the `except`
      clause's exception variable outside its suite. Falsifiable
      by grepping `node_inst_netdesc_op.py` for a bare `e` below
      `:162`: the guards at `:166` and `:170` must test a local
      captured inside the suite, and only `:276` may test the
      exception itself. A `NameError` here passes the PR and
      fails in the merge queue, because that path runs only
      under cluster CI.
- [ ] `_log_and_raise_on_error()` grows a defaulted
      `exception_class` argument and every pre-existing call site
      is unchanged, checked by grep. Without it the require stage
      cannot both publish through the helper and raise
      `AffinityConstraintUnsatisfiable`, which two parts of this
      plan had asked for at once.
- [ ] A create requesting **no** `require_*` constraint performs
      no more `_placed_instances()` reads than it does today,
      measured against `shakenfist/data/database_load_budget.yaml`.
      Hoisting the memo alone does not achieve this; the require
      stage must return before touching it. Stated as "no more
      than today" rather than "no more than before the hoist"
      because the affinity walk's unconditional call is the
      existing baseline, and skipping it when `inst.affinity` is
      empty should make this bullet pass with room to spare.
- [ ] `test_affinity` uses the `request_id` pairing only when
      that id is truthy, and fails rather than skips when neither
      the pairing nor adjacency identifies a pass. A match whose
      key is null on both sides would select the forced preflight
      pass and skip forever, which is the fourth route to the
      failure F3 exists to prevent.
- [ ] A create requesting **no** affinity at all still schedules.
      This is the cheap guard on the short-circuit: the affinity
      walk is what populates `by_affinity`, and
      `sorted(by_affinity, reverse=True)[0]` over an empty
      `defaultdict` is an `IndexError` on the majority path.
      Falsifiable by any scheduler test that places an instance
      with no `affinity` metadata, which is most of the existing
      suite -- the criterion is here because a load reduction
      that breaks every ordinary create is not a load reduction.
- [ ] A well-formed **binary** specification is accepted by all
      three entry points -- instance create and both metadata
      endpoints -- and not refused by the widened `int()` loop
      step 3 installs. The binary form's values are lists, which
      is precisely what that loop now 400s, so shape
      discrimination has to run above it. Two steps each correct
      in isolation compose into a validator that rejects the
      feature, and only a criterion spanning both catches it.
- [ ] The **409 is asserted at the API level**, not only as an
      exception type in the scheduler suite. F9 calls the
      `except`-clause ordering the entire mechanism, and a test
      which asserts `AffinityConstraintUnsatisfiable` was raised
      passes identically whether the create path answered 409 or
      507 -- so the scheduler suite cannot see the one mistake
      the ordering exists to prevent.
- [ ] The preflight abort guards at `node_inst_netdesc_op.py`
      `:166` and `:170` are exercised by a unit test. Both run
      only in the queue daemon under cluster CI, so anything
      wrong with them -- the `NameError` above, or a guard
      testing the wrong flag -- passes the pull request and fails
      in the merge queue.
- [ ] `docs/` states the `require_with_tag` bootstrapping dead
      end and its workaround in the same place as the constraint
      table. The first member of a group cannot be placed under
      the constraint that defines the group, which is the model
      working and reads as a bug; an operator who meets it
      undocumented files one.
- [ ] A **weighted** affinity specification still produces a
      populated `affinity_detail`, asserted by a unit test. This is
      the guard on the short-circuit's other edge: the `prefer_*`
      lists of a weighted spec are empty until step 5's mapping fills
      them, so a short-circuit landing without the mapping stops
      scoring affinity for every existing caller -- silently, since
      nothing fails, and `test_affinity` would skip green. The
      no-affinity bullet above does not catch it, and neither does
      the weighted-vs-binary ordering test, which belongs to the same
      step as the mapping.
- [ ] Both of `test_affinity`'s skip conditions are evaluated
      **per instance**, against inst2's unforced pass and inst3's,
      and the test skips if either is degenerate. Falsifiable by
      reading the two assertion call sites: each passes that
      instance's own events. Guards computed once from inst2 leave
      the inst3 assertion running against a pass that may have had
      no choice.
- [ ] `docs/user_guide/affinity.md` no longer says that affinity
      **filters** candidate hypervisors on the strength of the
      weights. It says so today, in an admonition, and that is the
      exact over-strong reading F2 identifies as the root of #3565 --
      so shipping this phase without touching it leaves the plan's
      central finding contradicted on the project's own user-facing
      page, next to the new section saying the opposite. Note the
      word is not simply wrong now: `require_*` does filter. The
      criterion is that the page says which half filters and which
      half ranks.
- [ ] **All three** preflight abort messages name affinity when the
      constraint is what failed, `:276` included. Falsifiable by
      grepping the three `AbortInstanceStart` sites for a branch on
      `AffinityConstraintUnsatisfiable`. `:276` is the one inside an
      `except` suite, so it is the easiest of the three -- which is
      how an earlier draft came to describe it without instructing
      it.
- [ ] The binary model has **cluster-CI** coverage, not unit
      coverage alone: a `prefer_with_tag` tier assertion and a
      `require_with_tag` refusal asserted as a 409. The project
      prefers functional tests where only one is possible, and the
      two behaviours least convincing under mocked placements -- the
      bootstrapping dead end and the namespace scope -- are
      properties of a real cluster's instance population.
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
