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
`prefer_without_tag` appear nowhere in the codebase --- not in
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
references support are still true --- admission does run before
affinity scoring, and `hard_max_cpus` is still absolute --- but
anyone checking D6 against the tree will bounce off the line
numbers first.

**3. #3565's traced mechanism is neither of the two the plan
argues about.** This is the finding that reshapes the phase.

A fully traced occurrence was posted to the issue on 2026-08-26,
after phase 4a fixed #3813 on 2026-08-24, so it is evidence from
a cluster where the spreader works. In it the candidate set had
already collapsed to **one node** by the time affinity was
scored. inst3, which requested anti-affinity (`{'first-node':
-100}`), scored that node `-100` --- correctly, having found and
matched inst1's tag --- and placed there anyway, because a scorer
given one candidate has nothing to do. The issue's own summary is
exact:

> the mechanism is slightly stronger than "soft affinity loses to
> resource filters under load": here it did not lose a tiebreak,
> it was **never consulted** in any meaningful sense.

The other half matters just as much. inst2 landing on inst1's
node --- the half of the assertion that *passed* --- was the same
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
(#3565 proper --- instances that should share a node do not).
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
around the CPU stage. The mechanism is unchanged --- an admission
filter empties the candidate set before ranking --- but any fix
aimed specifically at CPU admission would now miss.

**6. The forced-candidate retry re-forces the same node.** From
the same traced run: both instances were first refused by the
capacity guard on the single surviving candidate, and the retry
re-forced that same node rather than reopening the candidate set,
so the guard's refusal bought nothing. This is a real defect and
it is not this phase's --- recorded per F8.

**7. `test_affinity` is itself a user of the deprecated weighted
form.** It requests `{'first-node': 100}` and `{'first-node':
-100}` (`cluster_ci_tests/test_scheduler.py:80,99`). D6 asked
whether anything beyond the CI suite uses numeric weights and got
no answer; the CI suite is unambiguously a user, so the
transition mapping has a test exercising it from day one, and the
weighted form cannot be removed in the same change that adds the
binary one.

**8. The audit events already carry what the test needs.** The
2026-08-26 trace makes this point explicitly --- the events
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
steps 3 to 5 are the model. Either can land first.

**F2. #3565 is closed by the issue's candidate fix 2 --- assert
from the audit events --- and not by any of D6's three
positions.** This is the decision most likely to be argued with,
because it declines to change the scheduler in response to a
scheduler bug report.

The argument is finding 3. In the traced run the candidate set
was one node. Ask what each of D6's positions would have done
there. Position 1 (hard require only) turns the create into a
507, which the issue itself identifies as "the same ejection with
a different traceback" --- a placement flake traded for a #3772
refusal. Position 2 (soften the ceiling above a threshold
affinity score) has nothing to soften: the affinity target *was*
the surviving candidate and the instance was placed on it.
Position 3 (accept that co-location is not guaranteed under
concurrency, and change what the test asserts) is candidate fix 2
under another name, and is the one the evidence supports.

So the test asserts what soft affinity actually promises: that
the scheduler *scored* the affine node highest among the
candidates it had, or legitimately had no choice. It stops
asserting an outcome --- final co-location --- that no
documentation claims and the code has never guaranteed.

The honest framing is that #3565 was mostly a specification bug.
The product never promised co-location under contention; the test
assumed it did, and every investigation since has been looking
for the scheduler defect behind an assertion that was too strong.

**F3. The test skips, rather than passes, on a degenerate
candidate set.** If the scheduler had one candidate, the run
carries no information about affinity and must not be counted as
evidence that affinity works. A pass in that case is exactly the
false green finding 3 describes --- inst2's co-location "passing"
for the same reason inst3's anti-affinity failed. `skipTest` with
the candidate count in the message keeps the failure visible in
CI output without asserting something the run cannot support.

This is deliberately stricter than a test that merely stops
failing. Making the flake go away is easy and would leave the
suite claiming coverage it does not have.

**F4. Build the binary model, and keep the weighted form working
through a mechanical mapping for one release.** Per D6:
`require_*` become filters applied with the hard admission
filters, `prefer_*` contribute +/-1 per matching co-located
instance in the ranking term that already exists. Weighted specs
map positive to `prefer_with_tag` and negative to
`prefer_without_tag`, losing the magnitude, which is the point
--- the magnitude has never meant anything a caller could reason
about, since it is summed across an unbounded number of
co-located instances.

Finding 7 means the mapping is exercised from the moment it
exists, because `test_affinity` is a weighted caller. Whether
that test moves to the binary form in this phase or stays on the
weighted one as living proof the mapping works is left to step 4;
there is a real argument for the latter.

**F5. No new measurement campaign.** The 2026-08-19 correction
asked this phase to establish which mechanism operates before
spending its decision budget. Finding 3 establishes it, from a
trace taken after phase 4a's fix, at a level of detail no
sampling campaign would improve on --- it has the per-candidate
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
evidence about ranking --- including by this session, before the
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

**F8. The forced-candidate retry defect gets an issue, not a
fix.** Finding 6 is a real bug --- a retry that re-forces the
single node just refused cannot succeed --- but it is in the
preflight redirect path, not the affinity model, and fixing it
inside this phase would widen a phase that is already two phases
wearing one number. File it, reference it from #3565's closure so
the connection is not lost, and leave it.

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
              'prefer_without_tag': ['db'],
              'require_with_tag': ['ssd'],
              'require_without_tag': ['gpu']}}
```

A dict whose values are integers is the old form; a dict whose
keys are the four reserved names and whose values are lists is
the new one. Anything else is a 400. That check is mechanical,
which matters because the validator is the only place a caller
gets told they got it wrong.

The mapping in F4 is then: for each `tag: n` in a weighted spec,
emit `prefer_with_tag: [tag]` when `n > 0` and
`prefer_without_tag: [tag]` when `n < 0`. `n == 0` maps to
nothing, which is what it already means.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent | Status |
|------|--------|-------|-----------|---------------------|--------|
| 1 | high | opus | worktree | (shakenfist) Rewrite `test_affinity` onto the audit events, per F2 and F3. In `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_scheduler.py`, keep the three-instance setup unchanged and replace the two placement assertions (`:124-131`, whose
`['node']` arguments are on `:125` and `:129`) with assertions read from the scheduler's own events. The events are already fetched by `_add_scheduler_detail()` (`:19-35`), which filters `get_instance_events()` to messages starting with `schedule` -- factor its fetch out into a helper returning the events so both the detail-attachment and the assertions use one read, rather than fetching twice. Assert, for inst2: that `schedule have highest affinity` scored inst1's node in the winning tier. For inst3: that inst1's node was **not** in the winning tier. Then **skip, do not pass**, when `schedule final candidates` lists fewer than two candidates, with the count and the node in the skip message (F3) -- a single-candidate run carries no information and a green result there is the false pass finding 3 describes. Read the 2026-08-26 comment on issue #3565 first: it contains the exact event payloads from a failing run, including an `affinity_detail` that scored a node `-100` and placed there anyway, and it is the specification for what these assertions must distinguish. Do not assert final co-location anywhere -- that is the guarantee F2 establishes the product does not make. This runs only on `merge_group` (`docs/developer_guide/coding_rules.md:341-352`), so exercise it against sfcbr before proposing the commit. Commit subject: `tests: assert what soft affinity actually promises.` | Not started |
| 2 | low | sonnet | none | (GitHub) The #3565 disposition, once step 1 has merged. Comment on #3565 recording F2: the traced 2026-08-26 run shows a single-candidate set, so affinity was never consulted, and both halves of the assertion had the same cause; the issue is closed by candidate fix 2 rather than by a scheduler change; D6's three positions are disposed of by F2 and F7. Close it. Then file the forced-candidate retry defect from finding 6 as its own issue -- the retry re-forces the single node the capacity guard has just refused, so it cannot succeed -- with the event excerpt from that same comment, and cross-reference it from #3565's closing comment (F8). Also comment on `PLAN-ci-cloud-sizing`'s tracking of #3565 that its "needs a disposition in phase 0 before phase 4" is now satisfied. Include *(Triage assisted by Claude Code)*. | Not started |
| 3 | high | opus | worktree | (shakenfist) The binary model. Per F4 and the Design section: accept the new value shape under the `affinity` metadata key, validate it at `external_api/instance.py:1395-1406` alongside the weighted form (four reserved keys, list-of-string values, 400 on anything else), and consume it in `scheduler.py`. `require_with_tag` / `require_without_tag` become a filter stage placed **with** the admission filters and before affinity scoring, publishing a dropped map through `_log_and_raise_on_error()` like every other filter, with its own stage name so a refusal says which constraint ejected the node. `prefer_with_tag` / `prefer_without_tag` contribute +1 / -1 per matching co-located instance into the existing scoring loop (`:529-600`), which already has the per-candidate `affinity_detail` shape the events publish -- extend it rather than replacing it, because step 1's assertions read it. Unit tests in `shakenfist/tests/test_scheduler.py` beside the existing ordering cases. Note that a hard `require` which ejects every candidate must produce a clean no-candidate refusal, not a traceback. Commit subject: `Add binary affinity constraints to the scheduler.` | Not started |
| 4 | medium | sonnet | worktree | (shakenfist) The transition mapping, per F4. Map weighted specs mechanically at the point the scheduler reads them: positive value to `prefer_with_tag`, negative to `prefer_without_tag`, zero to nothing. Emit a deprecation event (not a log line -- this needs to reach an operator) the first time an instance with a weighted spec is scheduled. Decide and record whether `test_affinity` moves to the binary form or stays weighted: there is a real argument for staying, since finding 7 makes it the only automated proof the mapping works, and a separate binary case can be added beside it. Do not remove the weighted form; that is a later release, and the removal needs its own deprecation window. Unit tests for the mapping including the zero and mixed-sign cases. Commit subject: `Map weighted affinity onto the binary form.` | Not started |
| 5 | medium | sonnet | worktree | (shakenfist) Documentation. In `docs/`, state what soft affinity promises and --- more importantly --- what it does not: a preference is consulted when there is a choice, and a single-candidate placement is not a preference being honoured or violated. Document the four binary constraints, the weighted form's deprecation and its mapping, and the fact that `require_*` can make a create fail with no candidates where the weighted form would silently place anywhere. Include the diagnostic recipe, which is the durable output of this whole investigation: read `schedule have highest affinity` and `schedule final candidates` from the instance's events to tell "scored wrong" from "had no choice". `AGENTS.md` and `ARCHITECTURE.md` are unlikely to need touching; check rather than assume. Commit subject: `docs: say what soft affinity promises.` | Not started |
| 6 | low | sonnet | worktree | (shakenfist) Close-out. Set phase 6 to `Complete` in the master plan Execution table, confirm `docs/plans/index.md`'s arithmetic, and record in the phase status notes that #3565 closed on a test change rather than a scheduler change, with F2's one-line reason so a later reader does not reopen it looking for the missing fix. Commit subject: `scheduler: close out phase 6.` | Not started |

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
flake-suppressing change would be --- it refuses to report a pass
it cannot support.

**The rewritten test could pass on a cluster where affinity is
broken.** If the assertions only check that the affine node was
in the winning tier, a scheduler that scored everything equally
would pass. Mitigated by asserting the inst3 case as well ---
inst1's node must be *outside* the winning tier for a request
that scored it negative --- which a degenerate scorer fails.
Checked in step 1 by mutation: make the scorer ignore negative
contributions and confirm the inst3 assertion, and only that one,
fails.

**A hard `require` becomes a new way to make creates fail.** That
is what it is for, but it converts a class of silent
mis-placement into visible refusal, and an operator who adopts it
casually will see creates fail that used to succeed. Mitigated by
step 5 documenting the difference explicitly and by `require_*`
being opt-in --- no existing spec maps onto it, since the
weighted mapping produces only `prefer_*` forms.

**The weighted form outlives its deprecation.** Nothing forces
its removal, and finding 7 gives a standing reason to keep it
(the CI suite uses it). Mitigated by step 4 recording the removal
as needing its own release and its own window, rather than
implying this phase's mapping is the whole migration. Accepted:
one more release of a deprecated form is cheap.

**CI cannot verify step 1 on a pull request.** The `(collection)`
matrix is skipped on `pull_request`. Mitigated by the step's
brief requiring the rewritten test to be driven against sfcbr
first, which is the same rule and the same reason as phase 4b
step 4.

## Definition of done

- [ ] `test_affinity` makes no assertion about final instance
      placement. Checked with
      `grep -n "\['node'\]" cluster_ci_tests/test_scheduler.py`,
      which today returns exactly lines 125 and 129 --- the two
      assertion arguments --- and must return nothing inside an
      assertion afterwards. (Run at planning time: the more
      obvious `inst\['node'\]` matches nothing, because the
      assertions name `inst1`, `inst2` and `inst3`.)
- [ ] `test_affinity` skips, with a message naming the candidate
      count, when `schedule final candidates` lists fewer than
      two candidates.
- [ ] Mutating the scorer to ignore negative affinity
      contributions fails the inst3 assertion and no other test.
- [ ] A create with `require_with_tag` naming a tag no node
      carries fails with a no-candidate refusal naming that
      stage, not a traceback and not a silent placement.
- [ ] A weighted spec and its mapped binary equivalent produce
      the same candidate ordering, asserted by a unit test that
      builds both and compares.
- [ ] #3565 is closed with the F2 reasoning recorded on it, and
      the forced-candidate retry defect exists as its own issue,
      referenced from #3565.
- [ ] No document still tells phase 6 to rule out the
      lost-spreading mechanism: the master plan stub and D6 are
      both corrected by the planning commit.
- [ ] `docs/` states that a single-candidate placement is neither
      a preference honoured nor violated.
- [ ] `pre-commit run --all-files` passes.

## Future work

- **Removing the weighted affinity form.** Needs its own
  release and deprecation window; see F4 and the risk above.
- **The forced-candidate retry.** Filed by step 2 per F8.
- **Whether an activity metric belongs in the ranking at all.**
  Phase 00a's surviving observation: `cpu_load_1` measures
  activity, not occupancy, so a node packed with idle instances
  ranks ahead of a busier node with more room. Now that the
  capacity counters supply an occupancy measure the ranking could
  use instead, this is answerable --- but it is a ranking-model
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

**Before step 3 is written**, agree the Design section's value
shape --- the four reserved keys under the existing `affinity`
metadata key, distinguished from the weighted form by value type.
It is an API surface, so it is permanent from the moment it
ships, and it is cheap to change now and tedious once a
validator, a mapping, tests and documentation all reference it.
