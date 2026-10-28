# PLAN: Kerbside deployer phase 7 -- push audit

Planning effort: medium. Review effort: medium.

## Why this phase exists

Phases 1 to 6 landed in six pull requests here and five in two other
repositories, over four weeks. Each was reviewed on its own terms. This
phase runs `PUSH-AUDIT.md` over all of them together, which is the only
reading that can see what the phases did to each other.

The plan's shape invites that kind of defect. Phase 2 decided what Shaken
Fist does when `kerbside_url` is set and no `kerbside` group exists.
Phase 3 decided what `site.yml` does in the same case, and phase 5
rewrote most of phase 3's plays when the role moved to Kerbside. Phase 1
taught `internal_ca` to issue a certificate to any host, and phase 3 was
its first caller outside `allsf`. Every one of those seams is a place
where each side's review could be right and the pair wrong, and the
[tokens plan's audit](PLAN-kerbside-vdi-tokens-phase-10-push-audit.md)
found exactly that shape of defect -- a blocking, destructive one -- in
the configuration no phase had tested. It recommended that the seam
lens become a standing heading for cross-repository plans, so this plan
adds it (decision 6).

There is also an open report from inside this plan's feature. #4598
records Kerbside refusing a freshly minted console token with
`401 invalid token signature`, thirteen minutes after the role's "wait
for signing keys" task passed, in the lane phase 4 built. Nobody has
diagnosed it. The audit does not fix it, but the seam heading reads the
signing-key fetch with that report in hand (survey finding 7).

## Scope

**In scope.**

* The six merges in decision 1, under every `PUSH-AUDIT.md` heading:
  wave 1, the wave 2 mechanical sweep, the style judgment portion, and
  2a to 2d.
* The five merges in Kerbside and actions that phases 5 and 6 recorded,
  under those repositories' own runbooks (decision 2). Read-only.
* Two headings of this plan's own: the cross-repository seam lens (step
  7i) and the `plan-push-audit-phase` five-item floor applied to the
  cross-repository ranges, with the removed-consumer greps stated (step
  7j).
* The master plan's Future work entries that have no issue (survey
  finding 9), and the four seeded candidates S1 to S4. Disposition
  decides what each becomes.

**Out of scope.**

* Fixing what the audit finds. Decision 8 says where each kind of
  finding goes.
* Editing Kerbside or actions from this phase. Findings there become
  issues in those repositories (decision 2), and the operator pushes
  any actions change, by the master plan's precedent.
* The later merges that touch audited files (survey finding 8). They
  matter only where they change whether a finding still holds, which
  decision 3 covers.
* Diagnosing #4598. The seam heading says what it can about the fetch
  path; the issue keeps the diagnosis.
* Waiting for the weekly lane. Survey finding 6 explains; decision 13
  says what the test review uses instead.

## What the survey found

The master plan's Phase 7 section is short, and its `Merged` column was
complete for this repository but not for Kerbside. Three things are
corrected at source in the planning commit, so no later step redoes
them:

* Phase 6's `Merged` cell now names kerbside#592 (finding 2).
* The Phase 7 section's claim that phases 1 and 3 both edit `site.yml`'s
  certificate plays is replaced with what the ranges show (finding 5).
* The Phase 7 section, and the Planning effort note, give this phase a
  planning effort (finding 10).

1. **All six ranges are merge commits on `develop`.** Each has two
   parents, none contains a merge of its own, and
   `git merge-base --is-ancestor <sha> origin/develop` holds for every
   one:

   | Phase | PR | Merge | Commits | Files | Insertions / deletions |
   |-------|----|-------|---------|-------|------------------------|
   | 1 | #4430 | `12441652b` | 10 | 17 | +1493 / -99 |
   | 2 | #4442 | `80292df08` | 8 | 23 | +1794 / -73 |
   | 3 | #4465 | `b85005f0e` | 10 | 32 | +4735 / -95 |
   | 4 | #4479 | `0016c5dad` | 10 | 24 | +2323 / -171 |
   | 5 | #4591 | `48849831c` | 8 | 42 | +3377 / -3611 |
   | 6 | #4616 | `54fed79bcf` | 8 | 11 | +1170 / -35 |

   The counts include the plan documents. No range touches
   `shakenfist/protos/`, so the genprotos freshness check does not
   apply and wave 1 says so rather than running it. Every range touches
   markdown, so `tools/mermaid-lint.sh` does apply.

2. **Phase 6's `Merged` cell omitted its Kerbside landing.** Phase 6's
   step 3 landed as kerbside#592, merge `ea44e9364` on Kerbside's
   `develop`, six files, +45 / -25. The cell read `—`. It now reads
   `` `54fed79bcf` (#4616); kerbside `ea44e9364` (#592) ``.

3. **Five merges landed in other repositories, and none was audited
   there.** All five are two-parent merges and ancestors of their
   repository's default branch:

   | Phase | PR | Merge | Files | Insertions / deletions | What it carries |
   |-------|----|-------|-------|------------------------|-----------------|
   | 5 | kerbside#580 | `2093f2ed5` | 38 | +4955 / -8 | the `shakenfist.kerbside` collection: the role, its four helper scripts, `test_ansible_role.py`, Galaxy publishing |
   | 5 | kerbside#587 | `89f199456` | 17 | +514 / -510 | the sf-e2e lane deploying through the collection |
   | 5 | actions#181 | `17ea1889e` | 9 | +348 / -45 | the `collections` deploy-profile key, `ci-apply-deploy-profile.py` |
   | 5 | actions#195 | `31139125c` | 5 | +3 / -112 | `deploy-kerbside-on-shakenfist` retired |
   | 6 | kerbside#592 | `ea44e9364` | 6 | +45 / -25 | the co-location correction and the SF use-case page |

   The `plan-push-audit-phase` block says such a phase is audited in the
   pull request that lands it, and this audit cites that. Both
   repositories carry a `PUSH-AUDIT.md`, but none of the five pull
   request bodies mentions an audit, and neither the phase 5 nor the
   phase 6 plan records running one. There is nothing to cite.
   kerbside#580 is the largest range in the whole plan and carries the
   role that is the plan's main deliverable. Decision 2 audits the five
   ranges now. Kerbside's runbook has no `Merged`-column rule for
   `$RANGE` (zero mentions); actions' has one. Step 7c substitutes
   `<sha>^1..<sha>` in both.

4. **Phase 3's role no longer exists here.** #4591 deleted the nineteen
   files under `shakenfist/deploy/collection/roles/kerbside/` and
   `tools/ci-test-kerbside-role.sh`, and renamed
   `tests/test_kerbside_host_subject.py` to `test_spice_subject.py`
   (55% similar). The role lives at `ansible/roles/kerbside/` in
   Kerbside, and no Kerbside merge has touched `ansible/` since #580.
   A finding against #4465's role content is therefore checked at
   Kerbside's tip, not this repository's (decision 4). What #4465 left
   here is `site.yml`'s Kerbside plays, the node role's
   `kerbside_preflight`, `kerbside_credentials` and `kerbside_source`
   entry points, and `deploy/shakenfist_ci/spice_subject.py`.

5. **The master plan's interaction claim was half wrong.** The Phase 7
   section said phases 1 and 3 both edit `site.yml`'s certificate
   plays. Phase 1 touched fourteen files, all under
   `roles/internal_ca/`, CI and docs, and `site.yml` is not among them.
   Phases 2, 3, 4 and 5 each edit `site.yml`, and #4591 rewrote most of
   what #4465 added there (its hunks at `site.yml` 30-42, 140-164,
   162-199 and 784-1014 cover #4465's additions). The second claim,
   that phases 2 and 3 both decide what happens when `kerbside_url` is
   set and the group is empty, holds: #4442 in the node role's
   `kerbside_credentials` and the config template, #4465 in the plays
   gated on `groups['kerbside']`. The section now says so.

6. **The Kerbside lane left the merge queue on 2026-10-25.** #4653
   moved `debian-13-slim-tier`, the lane phase 4 put Kerbside on, from
   `functional-tests.yml` to `scheduled-tests.yml`, which runs Fridays
   at 15:00 UTC. `docs/developer_guide/ci.md` already says so. The
   master plan's success criterion "SF's merge queue runs a job in
   which the capability is advertised, a token is minted, Kerbside
   exchanges it, and the proxy's TLS endpoint verifies against the CA"
   was true when phase 4 landed and is not true as written now. The
   lane has not run since it moved: the last `scheduled-tests.yml` run
   was 2026-10-08, and the first with this lane in it is 2026-10-31.
   Seeded as S2 for disposition. Kerbside's own nightly sf-e2e lane,
   which deploys through the collection, passed on each of 2026-10-23
   to 2026-10-27.

7. **#4598 is open inside this plan's feature.** Filed 2026-10-21 from
   the merge-queue run for #4588: `test_exchange_returns_a_proxy_console_file`
   failed because Kerbside answered `401 invalid token signature` to a
   token Shaken Fist had just minted. The role's "Wait for Kerbside to
   fetch Shaken Fist's signing keys" task had passed thirteen minutes
   earlier, after one retry. The triage calls it a key-freshness seam
   between the two components and says #4588 could not have caused it.
   It carries `automated-fix-attempted` and no pull request references
   it. Seeded as S1. Step 7i reads the fetch path with it in hand.

8. **Twenty-five later merges touch audited files**, excluding plan
   documents and release notes. The ones that matter:

   | Merge | PR | Audited files touched |
   |-------|----|-----------------------|
   | `b157319988` | #4443 | `roles/internal_ca/tasks/bootstrap.yml` |
   | `48b827faaa` | #4548 | `roles/internal_ca/{defaults,meta,tasks/distribute_certificates.yml}`, `tools/ci-test-internal-ca.sh`, collection README (the `root:kvm 0440` key mode, #4416) |
   | `3baa3816a9` | #4538 | `config.py`, `test_config_redaction.py`, `standards.md`, `shakenfist_ci/base.py` |
   | `99e65e9707`, `dd5723c3db` | #4544, #4595 | `config.py` |
   | `a6cb54570e` | #4653 | `functional-tests.yml`, `.pre-commit-config.yaml`, `roles/node/tasks/kerbside_source.yml`, `tools/ci-deploy-profiles/kerbside-slim-tier.yml.j2`, `client/ctl.py`, collection README |
   | `776dc9e709`, `d3d5f15ca4`, `066816ee38`, `8a89247de0`, `93c31035a0` | #4437, #4468, #4490, #4569, #4650 | `shakenfist_ci/base.py` |
   | `e603d02a94`, `31e2f1b8b7` | #4636, #4455 | `test_headroom_gate_workflow_seams.py` |

   A finding against #4430's `internal_ca` changes is the one most
   likely to be "fixed since", because #4443 and #4548 reworked its
   tasks within three weeks.

9. **Three Future work entries have no issue.** `gh issue list --state
   all --search` in both repositories for "signing key rotation",
   "more than one Kerbside host" and "least-privilege scrape" returns
   nothing that matches. The other entries do: kerbside#300 and #301
   (no admin login without Keystone), kerbside#136 (no CA pin), #4483
   (`_version.py` in the restart hash). kerbside#444, which the
   least-privilege entry cites, is closed. The three are seeded
   together as S3.

10. **The master plan gives this phase no planning effort.** Its note
    covers phases 1 to 6. This plan uses medium, as the last four
    push audits did. The Phase 7 section now says so.

11. **A plan reference sits in a workflow file.** `functional-lanes.yml`
    line 116 reads "See docs/plans/PLAN-kerbside-deployer-phase-04-ci.md
    (D5, D6)". `git log -S` traces it to `9b479f7621`, a #4479 commit,
    from where #4653 moved it. The `plan-references-in-code` block makes
    a reference a diff adds a finding to fix. Seeded as S4 for 2a to
    confirm and grade.

12. **The floor's mechanical items are answerable now, and were
    checked.** Across all six ranges, a grep for private-key headers,
    literal passwords, `.home.stillhq` and RFC 1918 addresses finds the
    CI mesh addresses `10.0.1.10` to `.12` in `ci-make-inventory`'s
    test fixtures, already public in actions, and the nginx example
    `10.0.0.21` and `.22` in `kerbside.md`. Nothing private. No range
    adds a cron entry, a systemd timer or a scheduled workflow:
    certificate renewal (phase 1) runs when `site.yml` runs, not from a
    timer. Step 7j re-checks both against the five cross-repository
    ranges and records the greps.

13. **Phase 6 is complete.** Its Execution row and the `index.md` row
    agreed before the close-out; its merge is an ancestor of `develop`;
    no worktree or local branch remains (the remote branch
    `origin/kerbside-deployer-phase-06-docs` does, and is harmless);
    every falsifiable Definition of done item re-runs clean on
    `develop`, including the two navigation scripts and the three
    `kerbside.md` links; kerbside#579 is closed with the four nightly
    run links; kerbside#592 merged on 2026-10-24.

## Decisions

1. **The baseline is eleven ranges, read with `git diff`.** Six here,
   each `<sha>^1..<sha>`, from survey finding 1; five in Kerbside and
   actions, from finding 3. Wave 1's greps and the wave 2 mechanical
   sweep run once per range, in the range's own repository, and their
   output is pooled, as the runbook requires. Commit messages may be
   read for context, since finding 1 shows every range is this plan's
   own commits.

2. **The cross-repository ranges are audited now, read-only, under
   their own runbooks.** Nothing was audited in the pull requests that
   landed them (finding 3), so there is nothing to cite, and the
   alternative is to record that the role which is this plan's main
   deliverable was never audited at all. Step 7c runs each repository's
   wave 1 and mechanical sweep; the judgment steps read the five ranges
   beside the six, applying Kerbside's and actions' 2a to 2d briefs
   where they differ from this repository's. Nothing in either
   repository is edited from this phase: findings become issues there,
   and the operator pushes any change to actions. This is the decision
   most likely to be argued with, because it roughly doubles the
   surface. The answer is that kerbside#580 alone is larger than any
   range here, and an audit which skips it audits the wiring and not
   the thing wired.

3. **The judgment reviews read all eleven ranges together, and every
   finding is checked at the owning repository's tip.** The tips are
   `$TIP` = `51cc401933` here, `$KTIP` = `d79c48085` in Kerbside and
   `$ATIP` = `44ad110ac` in actions, each the default branch as fetched
   on 2026-10-28. Each candidate is recorded as one of:
   * **a finding**, if it is still true at the tip;
   * **fixed since by `<sha>`**, if a later commit removed it, which is
     a result but not a finding;
   * **out of scope, filed**, if the defect is in a later commit but
     breaks something this plan built.

4. **#4465's role content is checked at `$KTIP`.** Survey finding 4:
   the role moved. An agent reading `b85005f0e`'s diff of
   `roles/kerbside/` compares it with `ansible/roles/kerbside/` in
   Kerbside, and a finding there is a Kerbside finding, whichever range
   introduced it. The diff of #4591 is read for what the move changed,
   since the master plan's claim was that the move changed the role's
   home and not its behaviour.

5. **Every step runs as a sub-agent at the runbook's model and effort,
   with two raised.** 2a goes to opus rather than sonnet: its subjects
   are ansible plays, Jinja templates and shell, across three
   repositories, where the runbook's Python-shaped checks need
   translating rather than applying, and the pushdown and three-layer
   rules do not apply at all (no range touches `mariadb.py` or a proto
   file; 7b states that). The two added headings go to opus at high
   effort, because each is a narrow adversarial question.

6. **Two headings are added: the seam lens (7i) and the
   cross-repository floor (7j).** The tokens audit's own finding was
   that the heading no runbook carries is the one that found its
   blocking defect, and it asked for the lens to be a standing heading
   on cross-repository plans. This plan's seams are different values
   from the tokens plan's and are listed in 7i's brief. The floor is
   the `plan-push-audit-phase` block's five items, applied to the
   ranges whose repositories' runbooks were not run at landing time.

7. **The judgment agents are read-only and run in parallel.** They do
   not edit files, and do not run `pre-commit`, `tox`, `stestr` or any
   `tools/ci-test-*.sh` script. pre-commit's stash covers the whole
   repository, so parallel agents in one worktree silently revert each
   other, and the rootless test scripts write under `/tmp` and the
   checkout. An agent whose finding depends on running something names
   it, and the management session runs it. Only 7b and 7c, which run
   alone and first, and the management session, run anything. Wave 1
   in Kerbside and actions runs in their main clones at
   `/srv/kasm_profiles/mikal/vscode/src/shakenfist/{kerbside,actions}`,
   after `git status --short` is confirmed empty and the clone is on
   its default branch, and leaves them so.

8. **The audit's pull request records findings, and a separate pull
   request fixes the blocking ones.** This is what the
   `plan-push-audit-phase` and `plan-phase-landing` blocks require.
   * **Blocking** means a defect on a default branch now, in this
     plan's work. Its issue is filed with the `automated-fix-attempted`
     label at filing time, so the autofixer does not race the fix. A
     fix here lands in a follow-up pull request cut from `develop` in
     its own worktree, and that pull request also carries this phase's
     close-out: its `Merged` cell, and Complete for the plan. A fix in
     Kerbside is its own pull request there; a fix in actions is a
     reviewed diff handed to the operator.
   * **Advisory** findings are filed as issues in the owning
     repository. Each disposition, in this plan, gives the reason it is
     not this plan's to fix. That is declined in writing.
   * **Predating defects** are filed as issues and are not findings.
   * **No blocking findings** means no follow-up pull request is opened
     for the sake of one cell. Phase 7 sets itself and the plan to
     Complete in its own pull request, and records no `Merged` cell.

9. **Known issues are cited, not refiled.** Before filing anything, the
   management session runs `gh issue list --state all --search` in the
   owning repository for the finding's key terms, and writes the
   queries into the disposition. A finding that matches an open issue
   is recorded against it, and gets a comment only if it adds evidence
   the issue lacks. Issues to check first: #4598 (S1), #4097 (uncovered
   seams, items 1-3 and 5-6 open), #4367 (no headroom probe on
   Kerbside's lane), #4460 (`examples/cluster` `api_url` prefix), #4483
   (`_version.py` in the restart hash), #4416 (key mode, closed),
   kerbside#136 (no CA pin), kerbside#300 and #301 (Keystone-less
   admin), kerbside#444 (closed; the limitation holds).

10. **Wave 1 is relaxed the way the earlier audits relaxed it.**
    `pre-commit` and `tox` run against a tip plus this phase's prose,
    so a failure there is a failure on the default branch. It is
    recorded, checked for whether a range touched the failing module,
    and then wave 2 proceeds. The style greps, the mermaid render and
    the sweep are about the ranges, so they are not relaxed.

11. **A clean heading names what it read.** "Nothing found" is accepted
    only next to a list of the plays, templates, scripts, functions or
    pages examined. If the whole audit is clean, the master plan says
    so in one sentence.

12. **The record holds verdicts, paths, commands and counts only.** All
    three repositories are public. The management session checks the
    text this phase adds for private hostnames, addresses and
    credentials before each commit. CI run links and issue numbers are
    public, and may be cited.

13. **The audit does not wait for the weekly lane.** The test review
    (7f) uses the evidence that exists: #4479's merge-time runs, the
    2026-10-21 run in #4598, and Kerbside's nightly lane. If the
    management session wants a current run of `debian-13-slim-tier`
    before disposition, it asks the operator to dispatch
    `functional-tests.yml`, which accepts `workflow_dispatch`; it does
    not hold the phase for Friday.

## Step plan

Every step after 7a takes `$RANGES` to mean the eleven ranges in
decision 1, and `$TIP`, `$KTIP` and `$ATIP` to mean the three tips in
decision 3. The worktree is
`/srv/kasm_profiles/mikal/vscode/src/shakenfist/shakenfist-wt-kd-p07`.
The Kerbside and actions clones are the main ones named in decision 7.
`docs/components/kerbside/` in this repository is an automated import
of Kerbside's docs (`AGENTS.md`); no step edits it or reports its lag
as a finding.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7a | medium | opus | none | **Done in the planning commit.** Write this plan. Close out phase 6 (survey finding 2) in its own commit first. Correct survey findings 5 and 10 at source in the master plan's Phase 7 section and Planning effort note. Link this file from the Execution table and from `docs/plans/index.md`, with status In progress. Run `tools/check-plan-status.py` and `pre-commit run --all-files`. Commit subjects: "Close out kerbside deployer phase 6." and "Plan the kerbside deployer push audit." |
| 7b | low | sonnet | none | Wave 1 and the wave 2 mechanical sweep, this repository, in the worktree, running alone before any other step. Run `pre-commit run --all-files`, then `tox`, then `tools/mermaid-lint.sh` (every range touches markdown; check its exit status directly, never through a pipe). Do not run `tox -e genprotos`: no range touches `shakenfist/protos/` (survey finding 1); say so in the report. Then, **for each of the six ranges in survey finding 1 separately**, run the four style greps from `PUSH-AUDIT.md`'s wave 1 and the five commands of its wave 2 mechanical sweep, substituting `<sha>^1 <sha>` for `$RANGE`. Add two greps the runbook lacks, because most of these ranges are ansible and shell: `git diff <sha>^1 <sha> -- '*.yml' '*.yaml' '*.j2' '*.sh' \| grep -nE '^\+.*\b(TODO\|FIXME\|HACK\|XXX)\b'` and `git diff <sha>^1 <sha> -- '*.yml' '*.yaml' '*.j2' \| grep -nE '^\+.*\b(shell\|command):'`. Report every command and its literal output, or "no output", grouped by range. If `pre-commit` or `tox` fails, report the failing test names, and say whether any range touched the failing module (`git diff --name-only <sha>^1 <sha>`). Then stop (decision 10). Edit nothing. Commit subject, for the management session's record: "Record wave 1 of the kerbside deployer audit." |
| 7c | medium | sonnet | none | Wave 1 and the mechanical sweep in Kerbside and actions, after 7b, running alone. In each clone named in decision 7, first confirm `git status --short` prints nothing and `git branch --show-current` is the default branch (`develop` in Kerbside, `main` in actions); if either fails, stop and report. Then run that repository's own `PUSH-AUDIT.md` wave 1 as written (Kerbside: its `pre-commit run --all-files` and `tox`; actions: its `pre-commit run --all-files`), and for each of the five ranges in survey finding 3 separately, that repository's wave 1 style greps and wave 2 mechanical sweep, substituting `<sha>^1 <sha>` for `$RANGE`. Kerbside's runbook has no range rule for a plan audit; use the substitution anyway and say so. Add the two ansible greps from 7b's brief for kerbside#580 and #587. Report every command and its literal output grouped by repository and range, with the same failure rule as 7b. Leave both clones as found: `git status --short` must print nothing afterwards, and if a hook rewrote a file, restore it from a copy taken before running, not with `git checkout`. Commit subject: "Record wave 1 of the audit in Kerbside and actions." |
| 7d | low | sonnet | none | Style conformance, judgment portion, read-only (decision 7). Apply `PUSH-AUDIT.md`'s "Style conformance — judgment portion" brief to the six ranges here, read with `git diff <sha>^1 <sha>`. Most of the diff is ansible, Jinja and shell, so the Python rules apply only to `config.py`, `client/ctl.py`, `util/vdi_tokens.py`, `deploy/shakenfist_ci/{spice_subject,vdi}.py`, `tools/build-collection.py` and the tests. For the rest, the conventions are in `shakenfist/deploy/collection/README.md` and `docs/developer_guide/standards.md`: every role entry point has an `argument_specs.yml` entry; the restart-on-change contract (a task that changes a unit's inputs notifies its handler, and nothing else restarts); secrets under `no_log`; and the `plan-references-in-code` block, applied to task names, comments and workflow files (S4 is one; look for others). **Field rename / unit change:** `KERBSIDE_TOKEN_DURATION` is seconds, `internal_ca`'s renewal threshold is days; confirm each is named or documented as such everywhere it appears. Confirm each candidate against the file at `$TIP` and classify it under decision 3. Return findings with file, line, range and grade, or "Style checks passed" plus the files read. Commit subject: "Record the kerbside deployer style review." |
| 7e | medium | opus | none | 2a code quality, read-only (decision 7). Apply `PUSH-AUDIT.md`'s 2a brief and its four shared blocks to all eleven ranges together (decision 3), and Kerbside's and actions' 2a briefs to their ranges where they add a rule, taking 7b's and 7c's sweep output as input. The pushdown, cached-FK and three-layer rules do not apply: say so and move on. Areas worth the time: (1) `roles/internal_ca/tasks/{host_certificate,distribute_certificates}.yml` as #4430 wrote them and as #4443 and #4548 left them, for whether the any-host path and the `allsf` path are one implementation or two; (2) the node role's three Kerbside entry points (`roles/node/tasks/kerbside_{preflight,credentials,source}.yml`) against the Kerbside role's `validate.yml` and `register.yml` at `$KTIP`, for the same check written on both sides; (3) `site.yml`'s Kerbside plays at `$TIP` (lines 19-65, 86-121, 166-200 and the plays after 900) for logic that belongs in a role, since the master plan's open question 1 decided the plays own the group-to-role wiring; (4) the host-subject logic, which the tokens audit found as two hand-written tables: `deploy/shakenfist_ci/spice_subject.py` here, `ansible/roles/kerbside/files/kerbside-host-subject.py` in Kerbside, and `tests/fixtures/host-subject-renderings.json` there -- say whether #4591's rename left one implementation or two, and whether the fixture is shared or copied; (5) the three rootless scripts `tools/ci-test-{internal-ca,kerbside-cluster-config,kerbside-wiring}.sh` and Kerbside's `tools/sf-e2e/*.sh` for duplicated scaffolding; (6) `tools/ci-deploy-profiles/kerbside-slim-tier.yml.j2` against actions' `tools/ci-apply-deploy-profile.py`, for a key written by one and read by nothing. Grade S4 (survey finding 11). Apply the `comment-proportion` block to `site.yml`, whose Kerbside comments now run to some 60 lines before the first play. Check every candidate at the owning tip against survey finding 8. Return findings with file, line, range, grade and decision 3 classification. Commit subject: "Record the kerbside deployer code quality review." |
| 7f | medium | sonnet | none | 2b test review, read-only (decision 7). Apply `PUSH-AUDIT.md`'s 2b brief and its `functional-test-coverage` block to all eleven ranges. For each of the master plan's success criteria, say which test would have failed before the plan and passes after, and in which lane or job it runs today: the full-inventory deploy yielding a Kerbside-routed `.vv`; the bring-your-own path minting the key with no `sf-ctl` by hand; the second deploy restarting nothing (the `kerbside-slim-tier` profile's second-deploy check, and #4459); the merge-queue job (now weekly, survey finding 6 -- say what the move costs and whether the rootless tests `ci-test-kerbside-cluster-config.sh` and `ci-test-kerbside-wiring.sh` in `functional-tests.yml` 277-298 cover any of it on every merge); Kerbside's sf-e2e lane deploying through the collection; SPICE certificates renewing before expiry (does any test prove the renewal threshold fires, or only that a certificate is issued?). Read Kerbside's `kerbside/tests/unit/test_ansible_role.py` and say what it proves about the role beyond rendering. Read #4598's failure (S1) and say whether any test in either repository would distinguish a stale key set from a wrong one. Say whether the deploy-twice check can pass while a unit restarted, for instance if a handler is notified but the play ends before it runs. Return findings grouped by file, with grades and decision 3 classification. Commit subject: "Record the kerbside deployer test review." |
| 7g | medium | sonnet | none | 2c documentation review, read-only (decision 7). Apply `PUSH-AUDIT.md`'s 2c brief and all of its shared blocks to all eleven ranges. Pages here, read at `$TIP`: `docs/operator_guide/{kerbside,vdi_console_tokens,installation}.md`, `shakenfist/deploy/collection/README.md`, `docs/developer_guide/{ci,standards}.md`, `docs/release_notes/v07-v08.md`, `ARCHITECTURE.md`. In Kerbside at `$KTIP`: `docs/use-cases/shakenfist.md`, `docs/{installation,configuration,network-ports,testing}.md`, `ansible/README.md`, `demo/README.md`. In actions at `$ATIP`: `docs/{actions,ci,consuming}.md`. The falsifiable question is whether any fact is stated differently on two pages: the variable names and which are secrets, the certificate and key paths and their modes, the 365-day lifetime and the renewal threshold, the token duration default, the ports 5900, 5901 and 13002, the Kerbside version floor the soft dependency checks, the collection install step, and which lane runs the integration and how often (survey finding 6). Apply `plan-phase-references` to every page above, and `llm-doc-discipline` to what the ranges added to `ARCHITECTURE.md` and `AGENTS.md`. Check that the master plan's Execution table, its Future work and `docs/plans/index.md` agree with the tree after 7a. Return findings, or "No documentation gaps found" plus the pages read. Commit subject: "Record the kerbside deployer documentation review." |
| 7h | high | opus | none | 2d security review, read-only (decision 7). Apply `PUSH-AUDIT.md`'s 2d brief, its `path-traversal-review` block, and Kerbside's 2d brief, to all eleven ranges. **Credentials are the area that matters.** Work through, in order: (1) `kerbside_system_key`: minted by `roles/node/tasks/kerbside_credentials.yml`, carried through inventory, written into `sources.yaml` by the Kerbside role -- at every step say who can read it (file mode and owner, `no_log`, ansible output, the event log, `sf-ctl` output), and say plainly that it is a `system`-namespace key, so a Kerbside host compromise is a cluster compromise (kerbside#444's limitation; grade it, do not just cite it); (2) `kerbside_auth_secret_seed` and the password in `kerbside_sql_url`, as rendered into `kerbside.ini` -- mode, owner, and whether validation (`kerbside-check-ini.py`) can echo them; (3) the signing key: #4099 keeps it out of daemon environments and phase 4 added a live check -- confirm the check exists at `$TIP` and what it reads; (4) certificates: the proxy's key and certificate as `internal_ca` issues them to a host outside `allsf`, their modes after #4548, the CA bundle Kerbside trusts, the subject and SANs the proxy certificate carries against what the `.vv` pins, and what the renewal path does to a live proxy; (5) shell and templates: every `shell:` and `command:` task 7b and 7c listed, every `tools/*.sh` and `tools/sf-e2e/*.sh` argument that comes from inventory or a deploy profile, and every path built from a `kerbside_*_path` variable; (6) fail-open: each refusal in `kerbside_preflight.yml`, `kerbside_source.yml`, the Kerbside role's `validate.yml` and `site.yml`'s validation play -- construct the inventory that passes validation and deploys something wrong (a `kerbside_url` the two sides see differently, a colocated host whose `api_url` loops back, an empty group with `kerbside_url` set); (7) the usual sweep of the Python ranges. Return findings with severity, file, line, vulnerability class and recommended fix; critical and high must be resolved before this phase closes. Commit subject: "Record the kerbside deployer security review." |
| 7i | high | opus | none | **The cross-repository seam lens (decision 6), read-only.** Build the inventory of every value that crosses a repository boundary in this plan, and for each name the writer, the reader, and which side's tests fix its shape. Start from, and do not stop at: `kerbside_url`, written by the operator, rendered as `KERBSIDE_URL` by the node role and as Kerbside's token audience by the Kerbside role, with `site.yml`'s validation play asserting the two sides agree; `kerbside_system_key`, minted here and read as the source password there; the signing-key document at `/admin/vditokenpubkey`, fetched by Kerbside's source and waited on by the role's "Wait for Kerbside to fetch Shaken Fist's signing keys" task -- read this one with #4598 (S1) in hand, and say what the wait task actually proves, when the key is minted relative to the first fetch, what happens on a second `site.yml` run that rotates or re-mints, and how long a stale set can persist; the SPICE certificate subject and the CA, issued by `internal_ca` here and pinned by the proxy there; `kerbside_sources` and `kerbside_reserved_ports`, built by the node role's `kerbside_source` entry point and consumed by the Kerbside role; `kerbside_colocated`; the collection version floor `site.yml` checks against `galaxy.yml`'s version; the `collections` deploy-profile key, written in `tools/ci-deploy-profiles/kerbside-slim-tier.yml.j2` and read by actions' `ci-apply-deploy-profile.py`; and the `.vv` the suite's `vdi.py` reads. For each, answer: if the writing side changed this value's shape tomorrow, which test in which repository fails, and if the honest answer is "none", say so. Grade each seam **covered** (name the mechanism), **fixture-only**, or **uncovered**. Note that Kerbside's nightly sf-e2e lane and the weekly `debian-13-slim-tier` lane are the only places a real message crosses; say which seams each one exercises. Where fixture-only or uncovered, propose the cheapest change. Return findings, each marked for the repository it belongs to. Commit subject: "Record the kerbside deployer seam audit." |
| 7j | medium | opus | none | **The cross-repository floor (decision 6), read-only.** Apply the `plan-push-audit-phase` block's five-item floor to the five ranges in survey finding 3, giving each item a verdict or a sentence on why it does not apply, per range. For item 1, confirm each is a two-parent merge read against its first parent. For item 2, kerbside#587 changed `sf-e2e-functional.yml`, which runs nightly: say where its failure is reported (kerbside#579's history says an issue was filed by hand), and whether `demo-compose.yml` and `release.yml` changed what they report. For item 3, run the secrets and private-hostname greps from survey finding 12 over the five ranges and state them. For item 4, actions#195 removed `deploy-kerbside-on-shakenfist`, #4591 removed `roles/kerbside/` and `tools/ci-test-kerbside-role.sh`, and kerbside#587 replaced `tools/sf-e2e/deploy-kerbside.sh`'s approach: grep for each removed name across `shakenfist`, `kerbside`, `actions` and `client-python` under `/srv/kasm_profiles/mikal/vscode/src/shakenfist/`, excluding `docs/plans/`, `docs/components/`, `REVIEWS.md` and `.weaudit` files, and state the greps; say that the operator's private infrastructure repository could not be grepped. For item 5, kerbside#580 publishes a collection to Galaxy from `release.yml`: read the workflow and say whether a broken build publishes nothing or publishes something wrong, and whether the version check in `site.yml` would refuse a bad release or accept it. Return a verdict table, five rows by five ranges, with findings marked for their repository. Commit subject: "Record the kerbside deployer cross-repository floor." |
| 7k | medium | opus | none | Management session, not a sub-agent: grading and disposition are decisions. Pool 7b-7j's findings with seeded S1 to S4. Grade each blocking, advisory or predating, and give each a disposition (decision 8). Present the graded table to the operator before filing anything (see Back brief). Then run decision 9's duplicate searches, file the issues, and write a `### Disposition` table into this plan. Add an "Audit of phases 1 to 6" subsection to the master plan's Phase 7 section, and rewrite the success criterion survey finding 6 falsified so it says where the integration runs now. Point each Future work entry at its issue, or say why it stays an idea. If no finding is blocking, set phase 7 and the plan to Complete (decision 8, last case). Otherwise leave them In progress and cut the fix branch. Run `tools/check-plan-status.py` and `pre-commit run --all-files`. Commit subject: "Record the kerbside deployer audit disposition." |

7d to 7j are independent of each other. They run in parallel once 7b
and 7c have reported and the first back-brief gate has passed. The
management session records each report under `## Findings`, in a
sub-heading named for its step.

## Risks and mitigations

* **The audit rubber-stamps merged code.** Each phase had its own
  review, and a reviewer reading eleven reviewed diffs is inclined to
  agree with them. *Mitigation:* decision 11 makes every clean heading
  list what it read. 7h and 7i each ask the agent to construct the
  failing input, not only to confirm the happy path. 7k rejects a
  report that names no plays or files.
* **The runbook is Python-shaped and the diff is mostly ansible.** An
  agent applying the pushdown and three-layer rules to `site.yml` will
  report nothing and call it clean. *Mitigation:* 7b and 7c add the
  ansible greps; 7d, 7e and 7h each say which runbook rules do not
  apply and name the ansible conventions that replace them; 7e goes to
  opus (decision 5).
* **Findings against code that has since moved or changed.** The role
  moved repositories, and `internal_ca` was reworked twice. *Mitigation:*
  decisions 3 and 4. 7k spot-checks one "still true" claim per report
  at the owning tip.
* **Three repositories is enough surface to skim.** Roughly 21,000
  insertions across eleven ranges. An agent that reads the diffstat
  and reasons from file names will produce plausible findings that are
  not about this code. *Mitigation:* every brief names the files, and
  7k spot-checks two findings per agent against the tree before
  accepting the report.
* **Parallel agents interfere, across three clones.** *Mitigation:*
  decision 7. Only 7b and 7c, which run alone first, and 7k run
  anything; 7c leaves both clones as found and proves it.
* **Issues filed in Kerbside or actions are a public record from a
  partial reading.** *Mitigation:* the 7k back-brief gate, and
  decision 9's duplicate searches, both before anything is filed.
* **Predating or external defects are graded as this plan's.** #4598
  may be a Kerbside key-cache defect rather than anything a phase
  wrote. *Mitigation:* decision 8's predating grade, and the rule that
  the seam heading reads the fetch path but does not diagnose the
  issue.
* **Wave 1 is slow or fails for reasons outside these ranges.**
  *Mitigation:* decision 10, and the back-brief gate after 7c, so a
  broken default branch is reported before wave 2 is spent.

## Definition of done

* `## Findings` in this plan has a sub-heading for each of: wave 1
  here; wave 1 in Kerbside and actions; style; 2a; 2b; 2c; 2d; the seam
  lens; the floor; and disposition. Every heading except disposition
  names the files, plays, scripts or pages it examined.
* Wave 1 records literal output for 36 style-grep runs (six greps
  across six ranges here) and 30 sweep runs (five commands across six
  ranges), and the Kerbside and actions equivalents for five ranges.
  The check is that each range's sub-heading cites its merge:

  ```bash
  for s in 12441652b 80292df08 b85005f0e 0016c5dad 48849831c 54fed79bcf \
           2093f2ed5 89f199456 ea44e9364 17ea1889e 31139125c; do
    grep -c "$s" docs/plans/PLAN-kerbside-deployer-phase-07-push-audit.md
  done
  ```

  This prints a non-zero count for every range. Reading each
  sub-heading confirms the commands are there.
* Wave 1 says in one sentence that no range touches
  `shakenfist/protos/`, and `tools/mermaid-lint.sh`'s exit status is
  recorded.
* 7c's report ends with `git status --short` printing nothing in both
  clones.
* Step 7i grades every seam it lists, covered, fixture-only or
  uncovered, with the mechanism named for each "covered". Step 7j gives
  a verdict for each of the five floor items against each of the five
  ranges, and states the item 4 greps.
* Every finding carries a grade, a disposition (fixed in #NNNN, filed
  as #NNNN in a named repository, recorded on an existing issue, or
  declined with a reason) and the decision 3 classification. The
  searches behind each "no duplicate" claim are written in the
  disposition table.
* S1 to S4 are dispositioned. Every entry in the master plan's Future
  work names an issue, or says why it stays an idea.
* The master plan's success criterion about the merge queue says where
  the integration runs now, and `grep -c "merge queue runs a job"
  docs/plans/PLAN-kerbside-deployer.md` prints 0.
* `python3 tools/check-plan-status.py` exits 0, and
  `pre-commit run --all-files` is clean.
* The plan is Complete only when no blocking finding remains open. If
  it is Complete in this phase's own pull request, phase 7's `Merged`
  cell is left as `—`, as the `plan-phase-landing` block requires.

## Back brief

Before executing any step of this plan, back brief the operator on
your understanding of it and how the work you intend to do aligns with
it.

There are two gates beyond that, both cheap to stop at and expensive
to get wrong:

* **After 7c**, report whether wave 1 passed in all three repositories
  before spending on the seven parallel agents. If something failed
  for a reason unrelated to these ranges (decision 10), say so
  explicitly rather than presenting it as a finding.
* **In 7k, before filing any issue**, present the graded findings
  table to the operator. Issues are public, in three repositories, and
  the blocking or advisory split decides whether a follow-up pull
  request exists at all, and in which repository. Both are easier to
  agree before filing than to unwind after.
