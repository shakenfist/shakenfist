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

## Findings

### Wave 1, this repository (7b)

Run in this worktree on 2026-10-28 (UTC), at `$TIP` plus the two plan
commits. Edited nothing; `git status --short` printed nothing afterwards.

* `pre-commit run --all-files`: exit 0. All 16 hooks passed: Lint
  GitHub Actions workflows, skillsaw, Lint Ansible playbooks, Test the
  internal_ca role, Test the Kerbside cluster config, Test the Kerbside
  wiring, flake8, unit tests, from_db_by_ref namespace scoping,
  endpoints authenticate by default, API parameter locations,
  documentation links and anchors, no plan phase numbers in docs,
  signal handlers, plan statuses, mypy.
* `tox`: exit 0. `py3` OK, `flake8` OK, `cover` OK. stestr ran 6336
  tests in both `py3` and `cover`: 6181 passed, 155 skipped, 0 failed.
* `tools/mermaid-lint.sh`: `$?` printed `0` directly after the call. It
  rendered `ARCHITECTURE.md` and `docs/developer_guide/state_machine.md`
  (`ok` for each).
* genprotos: not run. `for s in <six>; do git diff --name-only $s^1 $s |
  grep protos; done` printed nothing, so no range touches
  `shakenfist/protos/`.

The per-range commands, run once per range with `<sha>^1 <sha>` for
`$RANGE`, were the four wave 1 style greps (lines over 120 characters,
`print(`, `etcd`, untagged `mariadb.get_all_*(`), the five wave 2 sweep
commands (TODO/FIXME/HACK/XXX, `# noqa`/`# type: ignore`/`pragma: no
cover`, `--stat`, `def test_` count, docs name-only, `subprocess.`/
`os.system`/`shell=True`), and the two ansible greps from the 7b brief
(TODO in yml/yaml/j2/sh, and `shell:`/`command:` in yml/yaml/j2). Every
command printed "no output" except those listed below; the `--stat`,
`def test_` and docs listings always print and are given for each range.

#### `12441652b` (#4430)

* `--stat`: 17 files changed, 1493 insertions(+), 99 deletions(-).
  `def test_`: 0. Docs touched: `docs/developer_guide/standards.md`,
  `docs/release_notes/v07-v08.md`, the collection README, and the plan
  files.
* `shell|command:` in yml/yaml/j2:
  ```
  336:+  ansible.builtin.command:
  393:+      ansible.builtin.command:
  ```

#### `80292df08` (#4442)

* `--stat`: 23 files changed, 1794 insertions(+), 73 deletions(-).
  `def test_`: 6. Docs touched: `standards.md`, `installation.md`,
  `vdi_console_tokens.md`, `v07-v08.md`, the collection README, and the
  plan files.
* `shell|command:` in yml/yaml/j2:
  ```
  362:+  ansible.builtin.command: "{{ sf_ctl_path }} ensure-kerbside-signing-key"
  372:+  ansible.builtin.command: "{{ sf_ctl_path }} bootstrap-system-key --key-from-stdin kerbside"
  449:+      ansible.builtin.command: "{{ sf_ctl_path }} show-config"
  ```

#### `b85005f0e` (#4465)

* `--stat`: 32 files changed, 4735 insertions(+), 95 deletions(-).
  `def test_`: 7. Docs touched: `standards.md`, `installation.md`,
  `vdi_console_tokens.md`, `v07-v08.md`, the collection README, and the
  plan files.
* `print(` in py: `349:+    print(json.dumps(out))` (the
  `kerbside-ready.py` helper, whose JSON on stdout is the interface the
  role parses; see 7c for the same line in Kerbside).
* `shell|command:` in yml/yaml/j2:
  ```
  860:+    shell: /usr/sbin/nologin
  876:+  ansible.builtin.command: python3 -m venv {{ kerbside_venv | quote }}
  881:+  ansible.builtin.command: "{{ kerbside_venv }}/bin/pip install -U pip uv"
  895:+  ansible.builtin.shell: >-
  938:+  ansible.builtin.command: >-
  949:+  ansible.builtin.shell:
  1062:+  ansible.builtin.command:
  1191:+      ansible.builtin.command:
  1300:+  ansible.builtin.command: date +%s.%N
  1352:+  ansible.builtin.command:
  1460:+  ansible.builtin.command:
  ```

#### `0016c5dad` (#4479)

* `--stat`: 24 files changed, 2323 insertions(+), 171 deletions(-).
  `def test_`: 17. Docs touched: `docs/developer_guide/ci.md`,
  `installation.md`, `v07-v08.md`, the collection README, and the plan
  files.
* `subprocess.` in py: `1266:+        subprocess.check_call([` (in
  `tools/build-collection.py`).

#### `48849831c` (#4591)

* `--stat`: 42 files changed, 3377 insertions(+), 3611 deletions(-).
  `def test_`: 1. Docs touched: `ci.md`, `standards.md`,
  `installation.md`, `vdi_console_tokens.md`, `v07-v08.md`, the
  collection README, and the plan files.
* Every grep printed no output.

#### `54fed79bcf` (#4616)

* `--stat`: 11 files changed, 1170 insertions(+), 35 deletions(-).
  `def test_`: 0. Docs touched: `ARCHITECTURE.md`, `installation.md`,
  `kerbside.md`, `vdi_console_tokens.md`, `v07-v08.md`, the collection
  README, and the plan files.
* Every grep printed no output.

### Wave 1, Kerbside and actions (7c)

Run on 2026-10-28 (UTC) in the two main clones, each clean on its
default branch before and after; `git status --short` printed nothing
in both at the end. actions was fast-forwarded from `bf3c4ce` to
`$ATIP` (one file, `.github/workflows/renovate.yml`). Kerbside's `HEAD`
and `origin/develop` were both `$KTIP`. Kerbside's runbook has no rule
for a plan-audit range; `AUDIT_RANGE="<sha>^1..<sha>"` was substituted
anyway. Its `wave1.sh` exits at its first fatal check.

**Kerbside, whole tree.** `pre-commit run --all-files`: exit 0, all 13
hooks passed (workflow lint, shellcheck, skillsaw, no source
line-number references in docs, whitespace, end of file, yaml, merge
conflicts, large files, flake8, unit tests, ansible collection lint,
ansible role test). `tools/audit/wave1.sh` with its default range and
tox: exit 0; `tox -eflake8` OK; `tox -epy3` OK, 571 run, 571 passed, 0
failed, 0 skipped; wave 1b printed PASS for raw print, bare except and
mechanical.

#### Kerbside `2093f2ed5` (kerbside#580)

* `wave1.sh` with `AUDIT_SKIP_TOX=1`: exit 3.
  ```
  FAIL: raw print() added in non-test source:
  +    print(json.dumps(out))
  ```
  The line is `ansible/roles/kerbside/files/kerbside-ready.py:69`, whose
  JSON on stdout is parsed by the role; none of the four helper scripts
  carries the `audit-allow-print` marker the script honours. Because
  the script stopped there, its bare-except check was run by hand by
  the management session: `git diff 2093f2ed5^1 2093f2ed5 -- '*.py'
  ':!*/tests/*' | grep -nE '^\+[[:space:]]*except[[:space:]]*:'`
  printed nothing. Recorded as finding W1 (disposition below).
* `wave2-mechanical.sh`: TODO/FIXME none; `# noqa` none; new `test_`
  functions 25 against 5 Python files changed; docs touched
  `docs/index.md`, `docs/plans/index.md`; new broad `except Exception`
  blocks at diff lines 64, 225, 561 and 607 (input to 7e and 7h); new
  asserts none; dependency changes none; alembic revisions none.
* TODO in yml/yaml/j2/sh: no output.
* `shell|command:` in yml/yaml/j2:
  ```
  901:+    shell: /usr/sbin/nologin
  917:+  ansible.builtin.command: python3 -m venv {{ kerbside_venv | quote }}
  926:+  ansible.builtin.shell: >-
  967:+  ansible.builtin.shell: >-
  1013:+  ansible.builtin.command: >-
  1024:+  ansible.builtin.shell:
  1128:+  ansible.builtin.command:
  1257:+      ansible.builtin.command:
  1376:+  ansible.builtin.command: date +%s.%N
  1428:+  ansible.builtin.command:
  1549:+  ansible.builtin.command:
  1676:+      ansible.builtin.command:
  ```

#### Kerbside `89f199456` (kerbside#587)

* `wave1.sh`: exit 0, PASS on all three checks.
* `wave2-mechanical.sh`: TODO none; `# noqa` none; new `test_` functions
  0 against 2 Python files changed; docs touched `ARCHITECTURE.md`,
  `docs/index.md`, `docs/testing.md`, `docs/use-cases/placement.md`,
  `docs/use-cases/shakenfist.md`; broad excepts none; asserts none;
  dependency changes none; alembic none.
* TODO in yml/yaml/j2/sh and `shell|command:` greps: no output.

#### Kerbside `ea44e9364` (kerbside#592)

* `wave1.sh`: exit 0, PASS on all three checks.
* `wave2-mechanical.sh`: TODO none; `# noqa` none; new `test_` 0 against
  0 Python files; docs touched `docs/configuration.md`,
  `docs/installation.md`, `docs/network-ports.md`,
  `docs/use-cases/shakenfist.md`; broad excepts, asserts, dependency
  and alembic changes none.

**actions, whole tree.** `pre-commit run --all-files`: exit 0, all 11
hooks passed (whitespace, end of file, merge conflicts, json, yaml,
executable shebangs, workflow lint, shellcheck, skillsaw, flake8, unit
tests). `python3 -m unittest discover -s tests -t .`: `Ran 787 tests`,
OK. `tools/ansible-syntax-check.sh`: exit 0, 17 playbooks.
`tools/gitleaks-scan.sh`: exit 1, `gitleaks not found`, since the binary
is not installed on this host. The management session re-ran it with a
wrapper that execs `ghcr.io/gitleaks/gitleaks:v8.21.2` in a container
with the working directory and `/tmp` mounted at their own paths:
positive control passed (both planted credentials reported), then
`642 commits scanned`, `no leaks found`, exit 0.

The per-range commands are actions' runbook's own: lines over 120
characters in py; files over 800 lines among changed py/sh/yml;
shellcheck over changed `*.sh` and `tools/run_remote`; removed
top-level keys in `*/action.yml`; removed inputs in the four named
reusable workflows; removed lines in `tools/ci-make-inventory.py`; new
`secrets: inherit`; new `timeout-minutes` in actions; new `runs-on`;
`REVIEWS.md` or `.vscode/` touched; TODO/FIXME; `# noqa`, `# type:
ignore` or `shellcheck disable`; docs name-only.

#### actions `17ea1889e` (actions#181)

* shellcheck (binary resolved from the pre-commit cache):
  ```
  In tools/deploy-collection.sh line 67:
  . /tmp/collection-venv/bin/activate
    ^-- SC1091 (info): Not following: /tmp/collection-venv/bin/activate was not specified as input (see shellcheck -x).
  ```
* Docs touched: `docs/actions.md`, `docs/ci.md`, `docs/consuming.md`.
* Every other grep printed no output.

#### actions `31139125c` (actions#195)

* Removed top-level keys in `*/action.yml` (the retired
  `deploy-kerbside-on-shakenfist`):
  ```
  19:-  base_user:
  23:-  primary:
  26:-  system_key:
  29:-  kerbside_public_fqdn:
  37:-  token_duration:
  41:-  kerbside_src:
  44:-  proxy_wheel:
  50:-  steps:
  ```
  Input to 7j's item 4.
* Docs touched: `ARCHITECTURE.md`, `README.md`, `docs/actions.md`,
  `docs/ci.md`.
* Every other grep printed no output.

### Style (7d)

Read-only, sonnet at low effort. Checked the Python conventions in
`config.py`, `client/ctl.py`, `util/vdi_tokens.py`,
`deploy/shakenfist_ci/{spice_subject,vdi}.py`, `tools/build-collection.py`
and the tests the six ranges add; every role entry point's
`argument_specs.yml` entry in `internal_ca`, `node` and the Kerbside role
at `$KTIP`; `no_log` on every task handling a secret; the restart-on-change
contract (the Kerbside role's only handler is the systemd daemon reload,
and restarts are driven solely by `register.yml`'s desired-state hash);
and units (`KERBSIDE_TOKEN_DURATION` is seconds everywhere,
`cert_expiration_days` and `cert_renew_days` are days everywhere). All
passed. The findings are plan references and stale citations:

* **Plan references added by #4479, still at `$TIP`** (S4 confirmed):
  `.github/workflows/functional-lanes.yml:116-117` (moved there by
  #4653), `deploy/shakenfist_ci/cluster_ci_tests/test_vdi_kerbside_exchange.py:124`,
  `tests/test_ci_deploy_profiles.py:30` and `:295` ("the plan's S4"),
  `tools/ci-deploy-profiles/kerbside-slim-tier.yml.j2:42`. A sixth, in
  `test_headroom_gate_workflow_seams.py`, was fixed since by
  `f8d3736f1e` (#4636).
* `roles/node/tasks/kerbside_source.yml:138` (#4591) cites
  `config.py:896-903` for the gateway ports; they are at 910-918 since
  #4595. Advisory.
* `shakenfist/deploy/collection/README.md:137-138` (#4430) says the SPICE
  key is still installed `0444` and cites #4416 as open, while the same
  file's table (`:189`) and `docs/operator_guide/upgrades.md:149` say
  `0440`. #4548 changed the mode and missed these lines: out of scope,
  but corrected in the follow-up because it is one sentence.

### Code quality, 2a (7e)

Read-only, opus at medium effort. The pushdown, cached-FK and
three-layer rules do not apply: no range touches `mariadb.py`, the
schema attribute models or protos. Nothing blocking; fourteen advisory
findings, A1 to A14, four of them predating or fixed since. What was
examined, by the six areas in the brief:

1. `internal_ca`'s any-host path (`site.yml:981-1013`) and `allsf` path
   (`:602-619`) are one implementation, parameterised through one YAML
   anchor; #4548's group check and key mode serve both. The hard-coded
   CA read at `site.yml:955` matches `external_api/admin.py:60-61`.
2. The node role's three Kerbside entry points against the Kerbside
   role's `validate.yml` and `register.yml` at `$KTIP`: checks on both
   sides are layered, not duplicated (empty key, the `system` username,
   `kerbside_url` equality here against non-emptiness there, ports built
   here and validated there, CA shape stricter here). The only copies
   are A4 and A11.
3. `site.yml`'s Kerbside plays (validation 165-315, deploy 918-1023),
   read with per-line blame: A5 to A8.
4. Host-subject logic: A10.
5. The rootless scripts: A9, A14. Kerbside's `tools/sf-e2e/*.sh` share no
   scaffolding beyond `set -euo pipefail`.
6. The deploy profile against actions' `ci-apply-deploy-profile.py`:
   every key the profile writes is read, unknown keys are refused, and
   `test_ci_deploy_profiles.py:78` pins the six top-level keys.

Findings:

* **A1, A2 (S4).** The five plan references 7d lists. Advisory, as the
  queue-performance audit graded its A3; fixed in the follow-up.
* **A3.** `kerbside-slim-tier.yml.j2:67-69` (#4479) says the SQL is
  idempotent "as Kerbside's own tools/sf-e2e/deploy-kerbside.sh is";
  kerbside#587 deleted that script. Advisory.
* **A4.** The stale `config.py` line numbers 7d found, plus a
  hand-written copy of five port defaults at `kerbside_source.yml:147-152`.
* **A5.** `site.yml`'s header runs to 89 comment lines before the first
  play, about 43 of them Kerbside (#4442, #4465, #4591). Lines 40-59
  restate `docs/operator_guide/kerbside.md` and the collection README;
  176-186 repeat `kerbside_source.yml:2-18`. Advisory: pointers.
* **A6.** `site.yml:999-1004` (#4465, #4591) recomputes the Kerbside
  role's defaults with literal fallbacks (`kerbside_pki_dir`, the three
  certificate names, `kerbside_file_owner`, `kerbside_group`), which
  live at `$KTIP` in `ansible/roles/kerbside/defaults/main.yml:64-77`.
  A default change in Kerbside leaves the certificate where the role does
  not look. Seam: `site.yml:976` already includes the role's `bootstrap`;
  including it with `public: true`, as `:255` does for `validate`, puts
  the role's defaults in scope. Advisory.
* **A7.** `site.yml:989-998` (#4479) decides the IP-vs-DNS SAN in play
  vars; it belongs in `internal_ca`'s `host_certificate` entry point.
* **A8.** `site.yml:202-238` (#4591): 33 lines of Jinja reimplementing
  Galaxy's version-range comparison, tested by the wiring script's case
  9. Advisory, low.
* **A9.** Four copies of the rootless-test scaffolding:
  `tools/ci-test-internal-ca.sh` (#4430), `ci-test-kerbside-cluster-config.sh`
  (#4442), `ci-test-kerbside-wiring.sh` (#4591) and Kerbside's
  `tools/ci-test-ansible-role.sh` (kerbside#580). Seam: one sourced
  helper here. Advisory.
* **A10.** Three host-subject renderers (`node.py:44-53`,
  `shakenfist_ci/spice_subject.py:27-60`, Kerbside's
  `kerbside-host-subject.py:38-73`) and two tables, Kerbside's a fixture
  generated once at `0016c5dad`. A rule change in `node.py` is caught
  for `spice_subject.py` only. Already #4097 item 6.
* **A11.** `test_ci_deploy_profiles.py:148-160` copies five loopback
  regexes from `kerbside_source.yml:66-71`; `:269` and `:275` copy the
  seed and key length floors. Advisory, low.
* **A12.** `kerbside-ready.py:69` lacks the `audit-allow-print` marker
  (finding W1 above). Advisory.
* **A13.** Unannotated functions in `shakenfist_ci/base.py:137`,
  `spice_subject.py:39`, `vdi.py` and Kerbside's four role scripts;
  the surrounding modules are wholly untyped and outside the mypy
  rollout. Python floor clean (both repositories `>=3.11`). Advisory,
  low.
* **A14.** Size candidates with seams, no split proposed: `site.yml`
  (1023 lines), `ci-test-kerbside-wiring.sh` (1161), Kerbside's
  `ci-test-ansible-role.sh` (1436). Cut the A9 scaffolding first.
* **Predating or fixed since, not findings.** P1: actions'
  `ci-apply-deploy-profile.py:141,383` writes `ci-deploy-profile.json`,
  which nothing reads (from `b525592`, before actions#181). P2:
  `internal_ca/tasks/distribute_certificates.yml:32-37` creates
  `/etc/pki/CA`, which nothing reads, since before #4430. P3: the
  world-readable key defaults #4430 kept, fixed since by `48b827faaa`.
  P4: the sixth plan reference, fixed since by `f8d3736f1e`.
* Mechanical sweep triaged: the one `subprocess.check_call` (#4479) is
  argv-form on a temp copy; #4430's `umask 277 && certtool` needs a
  shell for the umask; Kerbside's four `except Exception` blocks each
  reduce the error to a type name and none swallows silently;
  `kerbside-ready.py:89` closes its session on every path.
* Context for 7h and 7i: #4591 changed the role's content as well as
  its home. `diff -r` of `roles/kerbside/` at `48849831c^1` against
  `ansible/roles/kerbside/` at `$KTIP` shows 1384 changed lines.

### Tests, 2b (7f)

Read-only, sonnet at medium effort. Nothing blocking. For each success
criterion, the test that fails before and passes after, and where it
runs today:

| Criterion | Test | Runs |
|-----------|------|------|
| Full inventory yields a Kerbside-routed `.vv` | `test_vdi_kerbside_exchange.py` | `debian-13-slim-tier`, weekly since #4653 |
| Bring-your-own mints the key with no `sf-ctl` by hand | `ci-test-kerbside-cluster-config.sh` against a stub `sf-ctl`; `test_ctl.py` mocked | every merge, rootless only; no real lane |
| Second deploy restarts nothing | the profile's `redeploy_check`, comparing each unit's `InvocationID` after the whole second deploy | weekly |
| Merge-queue job with the feature on | the same lane | weekly, not the merge queue (S2) |
| Kerbside's sf-e2e lane deploys through the collection | `sf-e2e-functional.yml` | nightly; nine consecutive passes 2026-10-19 to 10-27 |
| SPICE certificates renew before expiry | `ci-test-internal-ca.sh` | every merge, but a 1-day and a 365-day certificate cannot pin the 90-day threshold: any value from 2 to 364 passes |

Findings, all advisory and still true at the owning tip:

1. **#4598 (S1).** `401 invalid token signature` means the kid is
   cached; a stale set gives "unknown signing key". Kerbside's
   `sf_token.py:134-149` folds every other `PyJWTError`, including
   `ImmatureSignatureError` for a future `iat` with leeway 0, into that
   message. Shaken Fist mints `iat`; Kerbside's unit tokens carry none.
   No test in either repository can tell stale from wrong, and the
   exchange test logs only the status and body. Reasoned from the code,
   not reproduced; a unit test with a future `iat` would confirm it.
2. Kerbside's `ci-test-ansible-role.sh` `run_register` deletes the
   deployed-state marker and skips the `kerbside-services` tag, so the
   restart decision and "marker only after ready" have no rootless
   coverage; only the nightly twice-deploy covers them.
3. `test_ci_deploy_profiles.py` does not assert that the Kerbside
   profile lists `kerbside-*.service` in `redeploy_check` or exports
   `SF_CI_EXPECT_VDI_CONSOLE_PROXY=1`; dropping either leaves the lane
   green.
4. The renewal threshold is unpinned, as in the table.
5. The bring-your-own path has no real lane.
6. `sf-client instance vdiconsolefile` runs against a real cluster
   nowhere (client-python).
7. S2, as in the table.
8. Kerbside's host-subject fixture is a recorded copy, not a live check
   (#4097 item 6).

The deploy-twice check cannot be fooled by a notified-but-unrun handler,
since it compares after the whole second deploy; its holes are that a
unit new on the second run is allowed and an unanswering host is
skipped. Also noted: `functional-tests.yml:279-280` and the
cluster-config script's header still say no cluster lane turns the
feature on, which has been false since #4479.

### Documentation, 2c (7g)

Read-only, sonnet at medium effort. Nothing blocking; eight advisory
gaps. Facts checked against the code and found consistent across every
page: the token duration, the key length minimums, the 365-day lifetime
and 90-day renewal, the certificate paths and modes, the ports, the
collection version range, the install step, and the sf-e2e schedule.
The ranges added no README growth, no `AGENTS.md` change and no ASCII
diagram; `ARCHITECTURE.md` grew by 10 lines of summary and links. The
master plan's Execution table agrees with `index.md`. Pages read: here,
`docs/operator_guide/{kerbside,vdi_console_tokens,installation}.md`,
the collection README, `docs/developer_guide/{ci,standards}.md`,
`docs/release_notes/v07-v08.md`, `ARCHITECTURE.md`; in Kerbside,
`docs/use-cases/shakenfist.md`,
`docs/{installation,configuration,network-ports,testing}.md`,
`ansible/README.md`, `demo/README.md`; in actions,
`docs/{actions,ci,consuming}.md`.

1. The collection README's `0444` contradiction (7d); `:155` "Until this
   release" is time-relative. Out of scope (#4548), fixed in the
   follow-up.
2. `docs/release_notes/v07-v08.md:105` calls `debian-13-slim-tier` a
   merge row; `ci.md:50` and the README say weekly. Out of scope
   (#4653), fixed in the follow-up.
3. `docs/operator_guide/kerbside.md:33` says Shaken Fist's merge queue
   deploys that shape. Same.
4. `kerbside.md:32` says put Kerbside on a hypervisor; Kerbside's
   `docs/use-cases/shakenfist.md:323-327` still calls co-location a CI
   expedient. kerbside#592 missed it. Finding at `$KTIP`.
5. Kerbside's `docs/network-ports.md:66-73` says its 5900 and 5901
   contend with hypervisor SPICE ports; Shaken Fist allocates console
   ports from a higher range, and `kerbside.md:51-53` says the defaults
   clear them. Finding at `$KTIP`.
6. No Shaken Fist page states the Kerbside 0.7.0 floor the role's
   `kerbside_package` default (`>=0.7.0`) enforces; the release note's
   discussion of 0.6.0 is about the cleanup defect and reads as the
   target. Finding.
7. `functional-tests.yml:279-280` says no cluster lane turns Kerbside
   on. False since #4479. Finding.
8. `kerbside.md:158-160` says `group_vars/kerbside` keeps `kerbside_url`
   the same on every host, then says to put it in `group_vars/all`, as
   `installation.md:242-244` and `site.yml:44-46` require. Finding.

### Security, 2d (7h)

Read-only, opus at high effort; one probe run under the worktree's
`.tox/py3` interpreter with `-B -I` and an empty environment, writing
nothing. No critical or high finding; two medium, five low, five
informational. The full examination per area is in the session's
record; what matters here:

* **Who can read `kerbside_system_key`.** At every step through the
  deploy it is on stdin under `no_log`, hashed in Shaken Fist's
  database, and `root:kerbside 0640` in `sources.yaml`. Every process
  running as `kerbside` can read it, Kerbside's database stores it in
  plaintext in `sources.password` on every scrape (predates the
  ranges), and it crosses the network in `POST /auth` every 60 seconds.
* **M1 (medium, advisory).** The key is minted with no scopes
  (`namespace.py:285-286`), so it is cluster admin: a compromise of the
  Kerbside host, the `kerbside` user or Kerbside's database is a
  compromise of the cluster. kerbside#444 is graded here, and it is
  worse than documented. Shaken Fist has enforced scopes since
  `451a0ef2d0`, so `kerbside.md:351-356`'s "no narrower key" is stale,
  and `bootstrap-system-key` has no scope option. kerbside#587's
  adversarial driver depends on the key's write power.
* **M2 (medium, advisory; low on a trusted backend network).** Nothing
  refuses an `http://` `api_url` on a dedicated Kerbside host
  (`kerbside_source.yml:52-81`), and the CI profile uses one. The
  trust-store workaround in `kerbside.md:345-350` probably fails: the
  venv's `requests` uses certifi, the venv has no system site-packages,
  and the units set no `REQUESTS_CA_BUNDLE`. Not run on a deployed host;
  reasoned from `bootstrap.yml:64-69,112-128`.
* **L1.** A Kerbside host whose `kerbside_system_key` is the deploy key
  passes every check and authenticates as `deploy`, because the equality
  assert in `kerbside_credentials.yml:42-49` runs once with the database
  host's variables and `kerbside_source.yml:27-34` checks non-emptiness
  only.
* **L2.** A trailing slash in `kerbside_url`, or a differing value on an
  `allsf` host other than the first, passes validation; see 7i's F3.
* **L3.** `kerbside_preflight.yml` compares exact uppercase names, but
  `config.py:196` exports whatever name the row has and pydantic-settings
  matches case-insensitively, so a lowercase `extra_config` row shadows
  the variable unrefused. Confirmed with the probe. Predating and
  separate: `SECRET_CONFIG_KEY_RE` is case-sensitive too.
* **L4.** Since #4442 `show-config` exempts values by runtime type, and
  `set-config` coerces all-digit values to `int`, so an all-numeric
  secret is printed, and `site.yml:793-802` logs that output on every
  deploy. Fix: exempt by the field's declared type.
* **L5 (Kerbside).** `kerbside_pip_extra` goes into shell tasks without
  `no_log` while the argument spec suggests `--index-url`.
* **I1 to I5.** Newlines are not refused in `kerbside_url`, the FQDN,
  the SQL URL or the seed before rendering; a `kerbside_public_fqdn`
  equal to an `allsf` host name yields a key satisfying that
  hypervisor's pinned subject; `kerbside_pki_dir` set to the SPICE
  directory on a co-located hypervisor overwrites QEMU's key; CI attaches
  the direct `.vv` with its password where the Kerbside one is redacted
  (`vdi.py:101-106`); actions' `deploy-collection.sh` installs profile
  collections with `--force` into a path that persists on static runners.
* **Holds.** #4099: the live check exists at
  `test_vdi_kerbside_exchange.py:255` and reads every `sf-*` main PID's
  environment, failing on an empty one. #4416 fixed since by `48b827faaa`.
  Certificates: newline and file-name validation in `internal_ca`
  satisfies the path-traversal block; the proxy verifies backends
  against the source CA and pinned subject. No injection or traversal:
  every shell task is argv-form or quotes validated names, every script
  uses `mktemp -d` with a trap, no argument comes from inventory, and
  Kerbside's Galaxy publish passes its token only through `env`.

### The seam lens (7i)

Read-only, opus at high effort. Fifteen seams graded; the full table is
in the session's record and summarised here.

* **Covered (the mechanism named):** `kerbside_url` equality (the
  wiring test on every merge, both lanes exchange); `kerbside_system_key`
  (the fetch authenticates with it, so the wait fails if refused); the
  public-key document's shape (the exchange in both lanes); the
  readiness wait, for what it checks; the source `ca_cert` (the wiring
  test compares bytes); proxy certificate placement (the wiring test
  against the installed role; the weekly lane's TLS handshake);
  `kerbside_sources` (the wiring test runs Kerbside's own checker from
  the release); the reserved ports' shape; the version floor (the wiring
  test installs the real release); the role interface; `test_env`;
  Kerbside's `.vv`; the `collections` key's shape; the `kid`, `aud`,
  `exp`, `sub` and `jti` claims; the host-subject rules by real message,
  weekly only.
* **Fixture-only:** the `iat` claim (Kerbside's test tokens carry none,
  and the nightly lane mints and verifies on one host, so it cannot see
  clock skew); a dedicated Kerbside host outside `allsf` (neither lane
  deploys one); the host-subject rules offline (#4097 item 6); the
  reserved-port values (hand-copied twice); the deploy-profile schema
  offline (copied in both directions).
* **Uncovered:** a trailing slash on `kerbside_url` (F3); signing-key
  rotation or re-mint across the seam (no lane rotates and then
  exchanges).
* **What only the weekly lane exercises (S2):** the cross-host fetch and
  verify, and so cross-host clocks; the Galaxy-form `collections` entry
  and the release accept path; the IP-SAN certificate; the explicit
  `.vv` and TLS checks. The strongest mechanism this plan built is the
  wiring test's install of the released role, which turns most ansible
  seams into contract tests against a real artifact on every merge.

**#4598, read against the code.** The run's base `ce8d0641b7` predates
#4591, so it deployed Shaken Fist's copy of the role; the wait task's
semantics are unchanged at `$KTIP`. The wait proves that a credentialed
`GET /admin/vditokenpubkey` returned 200 after the CA matched, and that
the row was stored; it does not prove the set contains the active kid or
that a token verifies. The key is minted before the first fetch by play
order. A second `site.yml` run never rotates, but if the direct read
fails `ensure_signing_key` mints over the stored set (F4). A stale set
persists about one 60-second tick while fetches succeed and without
bound while they fail. The 401 text is `BadSignature`, raised only when
the kid is cached and every decode failed, which includes a future
`iat` under leeway 0; a missing kid gives "unknown signing key". So the
issue's key-freshness reading is not what this code produces for a
stale set. The 401 is consistent with a cached key that did not verify
or with a token judged not yet valid on sf2, a different VM from the
minting host. The code cannot say which, the logs cannot distinguish
them, and the single-host nightly lane cannot reproduce the second.

Findings (grades proposed; the disposition decides):

* **F3.** A trailing slash on `kerbside_url` deploys cleanly, then every
  exchange returns 401 `token audience rejected`: `instance.py:2210`
  strips it from the audience, `kerbside.ini:25` renders it verbatim
  and `sf_token.py:67-75` compares exactly. The equality check passes
  because both sides see the same string, and no deploy-time step mints
  a token. The `rstrip` predates the plan; the single-variable contract
  is this plan's. Candidate blocking.
* **F4.** `_direct_get_cluster_config_value` returns `None` on
  `OperationalError` (`mariadb.py:24502-24506`), `ensure_signing_key`
  treats `None` as absent and mints over the stored set
  (`vdi_tokens.py:157-166`), and `kerbside_credentials.yml:64-68` now
  runs it on every deploy with `changed_when: false`. In-flight tokens
  die; Kerbside recovers through the unknown-kid path. Advisory.
* **F5.** The readiness wait proves a fetch, not a usable key set
  (`register.yml:210-245`); a deploy passes with F3, with clock skew, or
  with a set lacking the active kid. Advisory, Kerbside.
* **F1.** `BadSignature` is a catch-all and the fixtures carry no `iat`.
  Predating; Kerbside.
* **F6.** No real lane deploys Kerbside on a dedicated host, so phase 1's
  issuance to a host outside `allsf`, the reachability probe of such a
  host and the non-co-located reserved ports are fixture-only. Advisory.
* **F7.** The exchange test keeps no diagnostic evidence on a 401
  (`test_vdi_kerbside_exchange.py:132-169`). Advisory.
* **F8.** Shaken Fist's daemon ports are hand-copied twice into the
  reserved list (`kerbside_source.yml:147-152`,
  `ci-test-kerbside-wiring.sh:733`). Advisory.
* **F9.** The deploy-profile schema is copied in both directions;
  neither side reads the other's real file. Advisory.
* Cheapest changes, per seam: refuse a trailing slash in
  `kerbside_source.yml` with a wiring case (about 20 lines); rotate in
  the nightly lane and make `ensure_signing_key` raise on a read failure
  (about 35); have `kerbside-ready.py` print the cached kids and
  `site.yml` assert the active kid is among them (about 25); catch
  `ImmatureSignatureError` separately with a small leeway and add `iat`
  to the fixtures (about 25), and record the unverified claims and
  Kerbside's `Date` header on a failed exchange (about 15); a unit test
  comparing the reserved ports with `SFConfig` (about 20); have the
  wiring test validate the real profile with actions'
  `ci-apply-deploy-profile.py` (about 10). A dedicated-host lane needs
  an actions topology.

### The cross-repository floor (7j)

Read-only, opus at medium effort. Verdicts, five items by five ranges:

| Item | kerbside#580 | kerbside#587 | kerbside#592 | actions#181 | actions#195 |
|------|--------------|--------------|--------------|-------------|-------------|
| 1. Landing form | pass | pass | pass | pass | pass |
| 2. Unattended work reports failure | pass | pass | n/a | n/a | n/a |
| 3. Secrets, hosts, addresses | pass | pass | pass | pass | pass |
| 4. Removed names have no consumer | n/a | K-2, S-1 | n/a | n/a | pass |
| 5. Broken change fails closed | K-1 | n/a | n/a | pass | pass |

All five, and #4591, are two-parent merges and ancestors of their
repository's tip. Item 2: kerbside#587's `sf-e2e-functional.yml` files
an issue from the nightly job through `app/github-actions` (kerbside#579
was filed that way, not by hand as the brief said), and the last five
nightlies passed; outside these ranges, a failing nightly
`sanity_checks` is filed under the oVirt and OpenStack issue titles
(O-1, from `f303061`). Item 3: the greps for private-key headers,
literal passwords, the private domain and RFC 1918 addresses printed
only CI fixture values, three fixture passwords redacted in the record.
Item 4: `git grep` for `deploy-kerbside-on-shakenfist`,
`ci-test-kerbside-role`, `shakenfist.shakenfist.kerbside`,
`roles/kerbside` and `deploy-kerbside.sh` across `shakenfist`,
`kerbside`, `actions`, `client-python` and the `33fl` checkout on this
host, excluding plans, the docs mirror and review-tracking files, found
two stale citations and nothing executable; the operator's cloud
deployment repositories could not be grepped. Item 5: `site.yml`'s
version check refuses an out-of-range or unversioned collection and
accepts a bad release inside the range, which is correct: the guard
against a bad publish is the CI gate, which `5c73ecb` added after #580
(as #580 left `release.yml` there was none, and v0.8.0 shipped inside
that window: fixed since).

* **K-1 (Kerbside, advisory).** `release.yml`'s `sign-tag` needs only
  `build` and `build-proxy-wheels`, so a failed collection build still
  publishes both packages to PyPI while Galaxy and the GitHub release are
  skipped: a partial release, contrary to `sign-tag`'s own comment.
* **K-2 (Kerbside, advisory).** `docs/use-cases/multi-cloud.md:161`
  cites the deleted `tools/sf-e2e/deploy-kerbside.sh`.
* **S-1 (advisory).** `kerbside-slim-tier.yml.j2:68` cites the same
  deleted script (7e's A3).
