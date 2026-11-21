# Dependency-aware agent operations phase 4: documentation

## Prompt

Plan the next phase of
[PLAN-agent-operation-dependencies.md](PLAN-agent-operation-dependencies.md)
with the `next-phase` skill, after phase 3 merged as two pull
requests: the client half as
[client-python#453](https://github.com/shakenfist/client-python/pull/453)
(`a2c9d0d1f4`) and the functional test here as
[#4982](https://github.com/shakenfist/shakenfist/pull/4982)
(`6dde443468`).

Phases 0 to 3 built the feature. An agent operation carries
`depends_on` and `runs_after` edges with per-edge settle delays, the
sidechannel dispatcher evaluates them between `agent_operation_next()`
and `start_instance_executor()`, a dependent whose `depends_on`
dependency failed cascades to `abort` with the cause in
`dependency_failure`, the client can submit and await a chain, and two
smoke tier tests prove ordering and the cascade against a real
cluster on every pull request.

What is left is decision 11: the developer-guide page for the agent
operation model, and the example index. The master plan's phase 4 row
also assigns the API reference vocabulary, the two documented
ambiguities and the `client-python-k3s` adoption to this phase. The
survey found that the first two are already written and that the third
cannot be done here -- see below.

## Planning effort

High, and the master plan asks for no particular review effort for
this phase.

That is a deliberate choice for what looks like a writing phase. The
page itself is prose, but three things about it are judgement rather
than typing:

1. Four pages already carry dependency facts. A fifth which restates
   any of them creates exactly the drift
   `coding_rules.md` and the `llm-doc-structure` audit exist to
   prevent, and deciding what belongs where is the whole design of
   this phase.
2. The worked examples teach a pattern, and the pattern the master
   plan names does not work. Getting that wrong ships documentation
   which tells a reader to build something which aborts itself.
3. The phase has to decide what to do about the part of its own row
   it cannot deliver, rather than quietly dropping it.

## Scope

**In scope.**

- A new `docs/developer_guide/agent_operations.md`: the agent
  operation model -- one executor per instance, strict FIFO,
  independent outcomes, the timing budgets by reference,
  `depends_on` versus `runs_after`, the cascade to `abort`, retry --
  with worked examples in client code.
- The example index, as a section of that page, naming sibling
  repositories and what each exercises, with `client-python-k3s`
  first (decision 11).
- Registration in both `mkdocs.yml` and `mkdocs.yml.tmpl`.
- A release note for agent operation dependencies in
  `docs/release_notes/v07-v08.md`. None exists.
- Cross-links into the new page from the four existing pages which
  carry dependency facts, and the removal of any fact which the new
  page states and one of them also states.
- Phase 3's close-out, and this phase's registration, in the master
  plan's Execution table and `docs/plans/index.md`.

**Out of scope.**

- **`client-python-k3s` adopting dependencies.** Re-cut into phase 5
  by decision 1 below, for three independent reasons the survey
  found. Its plan file will live in that repository.
- Changing `AGENT_OPERATION_DEPENDENCY_WAIT`, its semantics, or the
  budget arithmetic. Decision 4 documents what ships; survey finding
  5 is a limitation of a shipped design and not a defect of it.
- Rewriting the two ambiguities documented in the API reference, or
  moving them. Decision 3.
- The declined mixed-type edges (#4824), the second example
  application (#4825), the cluster-side dependency walk defects
  (#4910) and the open-coded namespace predicate (#4887). All filed,
  all still open, none this phase's work.
- `docs/operator_guide/agent_operations.md`'s tuning content. It is
  complete and correct; this phase links to it.

## What the survey found

Phase 4's row was written by phase 0, before phases 1 to 3 executed,
and five of its claims have moved. Checked against `shakenfist` at
`47487f4207`, `client-python` at `a2c9d0d1f4` and
`client-python-k3s` at its `develop`.

1. **The API reference vocabulary is already written.** The row
   assigns "the dependency vocabulary in the API reference" to this
   phase. `docs/developer_guide/api_reference/instances.md:989` has a
   full *Declaring dependencies between agent operations* section: the
   two parameters, the 400 refusals and their deliberate
   indistinguishability, the capability token, the evaluation
   semantics of both edge lists, the wait budget, `dependency_failure`
   with its two non-state sentinels, and an `agentdependency` object
   section covering `settle_seconds` and its ceiling.
   `api_reference/agentoperations.md:7-14` documents
   `dependency_failure` on the operation view and links to that
   anchor. Phases 1 and 2 wrote them. Nothing is owed here.

2. **Both ambiguities are already written down**, in that same
   section, as two bolded subsections: *A dependent can read as
   `queued` after its dependency has failed* (decision 8's lazy
   cascade) and *A finished dependency is eventually deleted*
   (decision 9's swept dependency, including that absence is treated
   as failure and that a caller avoids it by keeping the dependent's
   deadline under `CLEANER_DELAY`). The row's third item is
   satisfied.

3. **Four pages carry dependency facts, not one.** Beyond the two
   above: `docs/operator_guide/agent_operations.md:255-285` documents
   `AGENT_OPERATION_DEPENDENCY_WAIT` at length -- the wall-clock
   anchor, the settle ceiling, the FIFO stall trade-off, and that
   lowering it is an API change -- and
   `docs/operator_guide/database.md:1576` and `:1652-1680` document
   the two `LONGTEXT NOT NULL` columns and the stored edge shape,
   linking to the `agentdependency` anchor. The new page is the
   fifth, which is what makes decision 2 the design of this phase
   rather than a formality.

4. **`docs/developer_guide/agent_operations.md` does not exist**, and
   neither does any example index: nothing under `docs/` mentions
   `client-python-k3s` except audit records in
   `docs/components/development/`. Those two are genuinely owed, and
   after findings 1 and 2 they are the only documentation this phase
   owes.

5. **The worked example the master plan names cannot be built as
   described.** This is the finding which matters.

   Decision 11 and the phase 4 row name `client-python-k3s`'s rolling
   update as the worked example, "rolling update as one submitted
   chain". Two things are wrong with that.

   First, there is no rolling update. `Cluster.update_os()`
   (`shakenfist_client_k3s/cluster.py:5651`), the body of `sf-client
   k3s update-os`, calls `instance_os_update()` (`:3157`), which calls
   `execute_and_await(instance_uuids, ['apt-get update', 'apt-get
   dist-upgrade -y'])` (`:2612`). That loops commands outer and
   instances inner, so every node runs `apt-get update` concurrently,
   then every node runs `dist-upgrade` concurrently, and the whole
   fleet is awaited as one. Per-node ordering of the two commands is
   free already, from the instance's FIFO queue. Adopting
   dependencies there would mean *writing* a rolling update -- a
   behaviour change to a shipped command, from simultaneous to serial
   -- not adopting edges in an existing one.

   Second, and worse, a fleet-wide chain submitted up front does not
   fit the budget this plan shipped. `note_dependency_wait_started()`
   (`shakenfist/operations/agentoperation.py:642`) writes the anchor
   on the first dispatcher pass which finds an operation blocked and
   is idempotent thereafter, deliberately: the docstring says an
   unconditional write would "leave a budget which can never be
   exhausted". `_dependency_budget_spent()`
   (`daemons/sidechannel/main.py:2227`) then tests `time.time() -
   anchor > budget`. The last link of a chain is submitted at the same
   moment as the first, reaches the head of its own empty instance
   queue immediately, and is found blocked on the first pass -- so its
   300 seconds must cover the entire chain ahead of it, not its own
   hop. An `apt-get dist-upgrade` alone can exceed that. A chain of
   more than two or three fast steps aborts its own later links on a
   default deployment.

   `runs_after` fails differently and no better: past the budget a
   `runs_after` operation runs anyway (`_dependencies_met()`'s
   asymmetry, `main.py:2244-2250`), so a rolling update built on it
   silently stops being rolling rather than failing.

   This is not a defect. The budget exists to bound the FIFO stall
   decision 1 accepts, and decision 2 chose 300 seconds against
   measurement. But it means the documentation must teach a bounded
   chain, and that the master plan's worked example needs decisions of
   its own.

6. **The client surface is unreleased.** `client-python`'s latest
   release is `v0.8.4` (2026-10-31), which predates phase 3's merge;
   `develop` is 31 commits ahead of it. `client-python-k3s` depends on
   `shakenfist_client >= 0.7.7` (`pyproject.toml:58`), so it cannot
   call `agent_dependency()` until there is a release carrying it and
   its floor is raised to that release. Making a release is Michael's.

7. Phase 0's one external prerequisite is still met:
   `client-python-k3s` knows `abort`
   (`AGENT_OP_FAILED_STATES`, `cluster.py:138`). Nothing is blocked on
   it.

Corrections for findings 1, 2, 5 and 6 are applied at source in the
planning commit -- in the master plan's phase 4 row, its phase table
and `docs/plans/index.md` -- so a later reader does not trip over
them. A later step should not redo them.

## Decisions

1. **The master plan's phase 4 is split: documentation is phase 4 and
   lands now, the `client-python-k3s` adoption becomes phase 5, and
   the push audit becomes phase 6.**

   Three independent reasons, any one of which would be enough.
   Survey finding 6: the k3s half cannot begin until a
   `client-python` release exists, which is not this session's to
   make and may be weeks away. Survey finding 5: it is a behaviour
   change to a shipped command rather than an adoption, and the chain
   shape the master plan assumed aborts itself, so it needs its own
   decisions about what to build. And it lands in a third repository,
   so it is a third pull request with its own plan file, review and
   CI.

   The alternative is to hold finished documentation behind an
   unreleased dependency. Phase 0 re-cut this table once already,
   folding the cross-instance phase into the evaluation phase, so
   re-cutting it is the plan's own precedent rather than a departure.

2. **Each fact keeps exactly one home, and the new page is the model,
   not a second API reference.** Field names, parameter shapes,
   refusal codes and the `agentdependency` object stay in
   `api_reference/`; configuration options and what to tune stay in
   `operator_guide/agent_operations.md`; column types and stored
   shapes stay in `operator_guide/database.md`. The new page explains
   the model a caller programs against and links to those three for
   detail.

   Concretely, the new page must not restate the default of
   `AGENT_OPERATION_DEPENDENCY_WAIT`, `CLEANER_DELAY` or any other
   option. It names the option and links. This is checkable, and the
   definition of done checks it.

3. **The two ambiguities stay where they are.** They are already
   written, well, in the API reference section, and
   `agentoperations.md` already links to that anchor. The new page
   links to them too. Moving them would churn a page which says it
   correctly and would break an anchor another page depends on.

4. **The worked examples teach a bounded chain, and the page states
   what a chain cannot do.** The page's dependency examples are
   short chains of fast steps -- the canonical one being put a file,
   then run it, then fetch the result, where fate-sharing is the point
   -- and the page says plainly when a chain is racing the wait
   budget and when it is not. **Corrected during step 4a's review**,
   because this text was too general and the page inherited that:
   edges are evaluated only for the operation at the head of its
   instance's queue, so a dependent on the *same* instance as its
   dependency never reaches the head until that dependency is
   terminal and retired, is never found blocked, and starts no budget
   at all, however slow the dependency was. The budget bites
   **across** instances, where the dependent reaches the head of its
   own queue immediately and spends the budget while the operations
   upstream of it run. So a cross-instance chain must fit inside one
   budget and fleet-wide rolling work stays submit-await-submit
   unless an operator has deliberately raised it, while a
   same-instance chain needs no arithmetic.

   **This is the decision most likely to be argued with**, because the
   obvious response to survey finding 5 is that the budget semantics
   are wrong and should be changed: a per-link budget which started
   when a link's dependency finished would make fleet-wide chains work
   and is what a reader expects from the phrase "dependency wait".

   I think documenting it is right, for now. The anchor is where it is
   because of decision 1 of the master plan: a blocked operation holds
   its instance's queue head and everything behind it, and the budget
   bounds *that*, not the caller's patience. A per-link budget would
   bound each hop while leaving the total hold unbounded, which is the
   property decision 2 explicitly refused when it declined to let the
   caller's deadline be the bound. Changing it is a design change with
   a measurement behind it, and belongs in its own phase or plan with
   that measurement, not in a documentation phase. What is not
   defensible is shipping documentation whose worked example aborts
   itself, which is what the master plan's row would have produced.

   Phase 5 inherits the question, because it has to decide what
   `client-python-k3s` actually does. If the answer there is that a
   real rolling update needs a per-link budget, that is a finding with
   a concrete use case attached, which is a much better basis for
   changing the semantics than this phase's reasoning would be.

5. **The example index is a section of the new page, not a page of its
   own.** Decision 11 calls for "an index section naming sibling
   repositories", and one page about agent operations with the
   examples at its end is easier to keep honest than a second page
   whose only content is three links. It names what each example
   exercises, so a reader can tell which one to read; it does not
   promise a second example, which is #4825's to deliver.

6. **A release note is part of this phase.** None exists for agent
   operation dependencies -- `docs/release_notes/v07-v08.md` carries
   the *cluster* operation dependency notes and the deadlines notes,
   and nothing for this feature. The deadlines plan's documentation
   phase set the precedent in one commit (`55fee2ef6f`): page, nav in
   both mkdocs files, release note, cross-links.

## Step plan

Steps run in sequence. Every one of them edits documentation in the
same worktree, and two agents running `pre-commit` or `mkdocs build`
against one tree will trip over each other's half-finished work.
4c and 4d also depend on 4a's headings and anchors existing.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | high | opus | none | **Write the agent operation model page.** New file `docs/developer_guide/agent_operations.md`, registered in the Developer Guide nav of **both** `mkdocs.yml` (the list at `:562-570`, which runs alphabetically: insert "Agent operations" before "Agent protocol") and `mkdocs.yml.tmpl` (the same list at `:114`). Both files, one line each: `mkdocs.yml` is what the build reads and `mkdocs.yml.tmpl` is what the docs sync regenerates it from, so an entry in only one is lost or invisible. The precedent commit is `55fee2ef6f`, which added `docs/operator_guide/agent_operations.md` the same way. Content: what an agent operation is and the one-executor-per-instance dispatch rule (`shakenfist/daemons/sidechannel/main.py`); strict FIFO per instance and independent outcomes, with the `;` not `&&` framing from the master plan's Situation section; the timing budgets named and linked, never restated (decision 2); `depends_on` as fate sharing versus `runs_after` as ordering, the cascade to `abort` and where the cause appears; per-edge settle delays; retry and attempts. Then worked examples in `shakenfist_client` code, using the phase 3 surface: `apiclient.agent_dependency(op)`, the `depends_on`/`runs_after` keywords on `instance_execute`, `instance_put_blob` and `instance_get`, `await_seconds=0` to submit without waiting, and `await_agent_chain()` to await a chain and be told which link actually failed. Read `client-python`'s `shakenfist_client/apiclient.py` on its `develop` for the real signatures before writing any example -- do not infer them. Every example must be a bounded chain, and the page must state that each link's wait budget starts when that link is submitted rather than when its dependency finishes, so the whole chain has to fit inside `AGENT_OPERATION_DEPENDENCY_WAIT`, and that fleet-wide rolling work therefore stays submit-await-submit unless an operator raised the budget (decision 4; the evidence is `shakenfist/operations/agentoperation.py:642` and `shakenfist/daemons/sidechannel/main.py:2227`). Link to `api_reference/instances.md#declaring-dependencies-between-agent-operations` for the field-level contract and for the two documented ambiguities rather than restating either. Finish with the example index (decision 5): a section naming `client-python-k3s` first, with what it exercises, and the other sibling client repositories which use agent operations, checked against those repositories rather than assumed. The repository forbids citing plan phases, decisions or steps in docs -- a pre-commit hook enforces it -- so write the reasoning without the citations. Commit subject: "Document the agent operation model." |
| 4b | medium | sonnet | none | **Release note for agent operation dependencies.** Append one section to `docs/release_notes/v07-v08.md`, following the register of the sections already there: they explain consequences for a reader upgrading, not APIs. Find the deadlines feature's section ("Agent operations are bounded by a deadline, not a fixed backstop") and the cluster-operation dependency notes at `:1210` and `:2070` and match their shape; do not restructure anything. Content: an agent operation may now name other agent operations it waits for, as `depends_on` (fate sharing: a failed dependency aborts the dependent without running it, and says which dependency in `dependency_failure`) or `runs_after` (ordering only); edges may cross instances, so a chain can span a fleet; there is a new terminal state, `abort`, which a client polling an instance's whole operation list may not recognise, and which only appears when somebody declared a dependency; the parameters are gated behind the `agentoperation-dependencies` capability token; and a blocked operation holds its instance's queue head, bounded by `AGENT_OPERATION_DEPENDENCY_WAIT`, which is why a long chain needs an operator's attention. Link to the new `docs/developer_guide/agent_operations.md` and to the API reference section rather than restating field shapes. Commit subject: "Announce agent operation dependencies." |
| 4c | medium | sonnet | none | **Cross-link the existing pages into the new one.** Four files, one link each, placed where a reader of that page would want it and worded for that page's audience: `docs/operator_guide/agent_operations.md` (which is tuning-facing -- link to the model page for what callers can express); `docs/developer_guide/api_reference/instances.md` at the head of the *Declaring dependencies between agent operations* section (`:989`); `docs/developer_guide/api_reference/agentoperations.md` near its `dependency_failure` paragraph (`:7-14`); and `docs/operator_guide/database.md` at `:1652`, where the edge columns are described. Use relative links in the form those pages already use -- note that the two API reference pages use both absolute (`/developer_guide/...`) and relative (`../../operator_guide/...`) forms, so match the surrounding lines rather than picking one. Add nothing else to any of the four. Commit subject: "Point the existing pages at the model page." |
| 4d | high | opus | none | **Remove what is now said twice, and prove the docs build.** Five pages now carry dependency facts: the new `docs/developer_guide/agent_operations.md`, `docs/developer_guide/api_reference/instances.md`, `docs/developer_guide/api_reference/agentoperations.md`, `docs/operator_guide/agent_operations.md` and `docs/operator_guide/database.md`. Read all five dependency-related sections and find every fact stated in more than one of them. For each, decide which page owns it by decision 2's rule -- contract in `api_reference/`, tuning in `operator_guide/agent_operations.md`, storage in `operator_guide/database.md`, model in the new page -- and cut it from the others, leaving a link. Pay particular attention to any number: a default which appears on two pages will eventually disagree with itself, and `AGENT_OPERATION_DEPENDENCY_WAIT`'s 300 seconds, `CLEANER_DELAY`'s 3600 and `DISPATCH_CHECK_INTERVAL`'s 5 are each at risk. Do not rewrite prose you are not deduplicating, and do not touch the two ambiguity subsections in `instances.md`. Then run the repository's documentation checks: `pre-commit run --all-files` (which includes the mkdocs build, the link and anchor resolver, and the plan-phase citation check), and confirm the new page appears in the built nav rather than only in the source tree. Report which facts you cut and from where. Commit subject: "Give each dependency fact one home." |

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| The new page restates what the API reference owns, and the two drift. | Decision 2 assigns ownership before any writing, step 4d reads all five pages against it, and the definition of done greps for the three defaults which are most likely to be duplicated. The management session reads the new page in full before committing 4a. |
| A worked example does not actually work, because it was written from the plan rather than from the client. | Step 4a's brief requires reading `client-python`'s `apiclient.py` on `develop` for the real signatures. The management session checks each example's calls against that file, and checks that no example builds a chain whose links cannot all fit in the wait budget. There is no cluster here, so the examples cannot be run; that limit is stated in the definition of done rather than hidden. |
| The nav entry lands in `mkdocs.yml` only, and the next docs sync silently drops the page from the published site. | Both files are named in the brief, with the precedent commit, and the definition of done greps both. |
| The page cites a plan phase or decision, which a pre-commit hook rejects. | Named in the brief. The hook runs in `pre-commit run --all-files`, which step 4d runs and the management session runs again before each commit. |
| Phase 5 never happens, so the example index promises an adoption which does not exist. | The index describes what `client-python-k3s` exercises **today**, which includes agent operations and the `abort` state, not what phase 5 may add. Decision 5 says so, and the definition of done checks the page makes no forward promise. |

## Definition of done

Each item is checkable, and the command is given where there is one.
An item which cannot be checked is reported as such, not ticked.

- `docs/developer_guide/agent_operations.md` exists. Check: `test -f
  docs/developer_guide/agent_operations.md`.
- It is registered in both nav files. Check: `grep -c
  'developer_guide/agent_operations.md' mkdocs.yml mkdocs.yml.tmpl`
  prints 1 for each.
- The page does not restate a configuration default. Check: `grep -nE
  '300 seconds|3600|CLEANER_DELAY is|five seconds'
  docs/developer_guide/agent_operations.md` finds nothing. The option
  names may appear; their values may not.
- The page states when the wait budget is spent and when it is not,
  which is the one thing a reader could otherwise get wrong from every
  other page. Check: the page names the budget, says it is anchored
  when the dispatcher first finds an operation blocked, and
  distinguishes a same-instance edge, which never starts it, from a
  cross-instance edge, which does. Read by the management session
  against the single call to `_dependencies_met()` on
  `agent_operation_next()` in
  `shakenfist/daemons/sidechannel/main.py`. Not greppable, so it is
  read.
- Every client call in every example exists in `client-python` on
  `develop`. Check: for each symbol used, `git -C ../client-python
  grep -n '<symbol>' origin/develop -- shakenfist_client/apiclient.py`
  finds a definition. The examples themselves are **not** run: there
  is no cluster here, and that is reported as unchecked rather than
  ticked.
- A release note section for agent operation dependencies exists.
  Check: `grep -c 'depends_on' docs/release_notes/v07-v08.md` is
  greater than it was at `47487f4207`, and the new section names
  `abort`.
- All four existing pages link to the new one. Check: `grep -l
  'developer_guide/agent_operations.md\|agent_operations.md'` matches
  `docs/operator_guide/agent_operations.md`,
  `docs/developer_guide/api_reference/instances.md`,
  `docs/developer_guide/api_reference/agentoperations.md` and
  `docs/operator_guide/database.md`.
- No fact about dependencies is stated on two pages. Step 4d's report
  lists what it cut; the management session spot-checks three of them
  and reads the remaining overlap itself. This is a judgement, so it
  is recorded as a read rather than as a command.
- The page makes no promise about a `client-python-k3s` feature which
  does not exist. Check: `grep -niE 'will adopt|rolling update'
  docs/developer_guide/agent_operations.md` finds nothing, or finds
  only a sentence saying rolling work is not a single chain.
- `pre-commit run --all-files` passes, including the documentation link
  and anchor check, the plan-citation check and
  `tools/check-plan-status.py`. **Corrected during execution:** this
  criterion said `pre-commit` includes the mkdocs build. It does not --
  there is no such hook in `.pre-commit-config.yaml`, and the build is
  a separate CI job -- so `tox -e docs` is run as well, and must end in
  `congratulations` with no line naming a file this phase changed.
- Phase 3 reads `Complete` in the master plan's Execution table with
  both of its merge commits, and `docs/plans/index.md` agrees. Check:
  `python3 tools/check-plan-status.py`.

## Execution notes

Departures from the plan, recorded as they happened. Dates are AEDT.

1. **2026-11-21.** Back brief answered: the phase split is accepted;
   the wait budget is to be documented rather than changed; the
   `client-python` release question is to be asked again when phase 5
   is planned rather than guessed at now; and this session may push
   and open the pull request.
2. **Step 4a's brief was corrected before it was sent.** The step row
   asks for an example index naming "the other sibling client
   repositories which use agent operations". There are none:
   `client-python-ova`, `andris`, `clingwrap` and `cloudgood` contain
   no non-test module which creates an agent operation, and only
   `client-python-k3s` does, in two. The index therefore has one
   entry, which is what shipped.
3. **Decision 4 and one done-criterion were corrected at source**
   during step 4a's review, and the page with them. Both said that a
   link's wait budget starts when that link is submitted, which is
   true only across instances. `_dependencies_met()` is called on the
   operation `agent_operation_next()` returns and on no other, so a
   dependent queued behind its own dependency on one instance is not
   evaluated until that dependency is terminal and retired, by which
   time its edge is satisfied: no anchor is written and no budget is
   spent. As written, the page told a reader that the canonical
   same-instance chain was racing a clock it never races, and it
   contradicted its own fleet example, which puts a package upgrade
   inside a chain.
4. Steps ran in the planned order, one at a time, for the reason the
   step plan gives: they all edit documentation in one worktree.
5. **A premise in step 4a's brief was wrong, and the agent repeated it
   as a finding.** The brief said `pre-commit run --all-files`
   includes the mkdocs build; there is no such hook, and the build is
   its own CI job. Step 4a's report echoed the claim, step 4b's agent
   noticed it was not in the hook list and said so, and every step
   from 4b on ran `tox -e docs` separately. A done-criterion carried
   the same error and is corrected above.
6. **Three of step 4d's cuts were restored during review.** Each had
   removed a clause its own page needed: that `dependency_failure`'s
   `state` can hold `missing` or `unreadable`, from the page which
   documents that field; the reason a chain-awaiting client must treat
   `expired` as terminal, from the paragraph whose point that is; and
   why the wait anchor is cleared on retry, from the sentence
   describing the column. Deduplication is worth a link, not a reader
   having to follow one to understand the sentence in front of them.
7. Step 4d found one contradiction rather than a duplicate: the
   operator guide said the dependency wait contains the dependency's
   own queue time, so a deep chain can exceed the default, which is
   true only across instances. It was fixed, and it is the same error
   note 3 corrects.

## Future work

- **A per-link dependency wait budget.** Decision 4 documents the
  budget as it ships: every link's wait starts when that link is
  submitted, so a chain must fit inside one budget. A budget which
  started when a link's dependency finished would make fleet-wide
  chains work and is what the phrase "dependency wait" suggests, but
  it would leave the total queue-head hold unbounded, which is the
  property the master plan's decision 2 refused. Phase 5 is where the
  use case for changing it will be concrete, so the question is
  recorded here rather than answered.
- **The master plan's worked example.** "Rolling update as one
  submitted chain" is not buildable at the default budget and is not
  what `client-python-k3s` does today. Phase 5 decides what it does
  instead; this phase records the gap.

## Back brief

Before executing any step, back brief Michael on the plan as
understood, and get answers to these:

1. **The split.** Decision 1 turns the master plan's phase 4 into a
   documentation phase and moves the `client-python-k3s` adoption to a
   new phase 5, pushing the push audit to 6. That re-cuts the
   published phase table. Agree, or keep one phase and hold the
   documentation until a `client-python` release exists?
2. **The budget decision.** Decision 4 documents the
   budget-starts-at-submission behaviour and declines to change it in
   this phase. Agree, or should this phase instead stop and propose
   the semantics change, leaving the documentation until the answer is
   known?
3. **The release.** Phase 5 needs a `client-python` release carrying
   phase 3's surface, and `client-python-k3s`'s floor raised to it.
   That is Michael's to do and nothing in phase 4 depends on it; it is
   asked here only so phase 5 is not planned against an assumption
   about when it happens.
4. **Opening the pull request.** This phase is one pull request in
   this repository with no cross-repository ordering, so it can be
   pushed as soon as it is green. May this session open it?
