# Deployment options phase 7: push audit

Master plan: [PLAN-deployment-options.md](/components/kerbside/plans/PLAN-deployment-options/).

## Planning effort

The master plan gives this phase no effort level; it only carries
the push-audit shared block. This plan was written in a low-effort
session. The survey derived the audit range by hand, merge by merge,
and checked it against the merge history and against the audit
tooling's known defects, so it was not cut short. Review effort:
medium. The judgment here is in the range, not in the steps, which
follow `PUSH-AUDIT.md`.

## Scope

In scope:

- Running `PUSH-AUDIT.md`, waves 1 and 2, over everything the plan
  landed. That is seven merges, listed under *Audit range*.
- Triaging every finding as blocking or advisory, and recording it,
  with the reason, in this plan's *Outcome*.
- Filing problems that predate the plan as issues.
- One issue for `tools/audit/plan-range.sh` (finding 3), if Mikal
  approves the wording.

Out of scope:

- Fixing findings. They land in a follow-up pull request after this
  one merges (decision 4).
- Work other plans own: the index's Proxmox use-case row
  (`PLAN-proxmox-source.md` phase 6), #602 and #629 (the role), and
  #553 (a first-party image).
- #491 itself. This plan works around it (decision 1) and does not fix
  it.

## Audit range

Every merge below is a merge commit on `develop`'s first-parent
chain. Its diff against its first parent is the whole of what that
pull request landed.

| Merge | PR | Date | What | Non-plan content |
|-------|----|------|------|------------------|
| 200d6b3 | #555 | 2026-10-11 | Phase 1: survey, matrix, scaffolding | `docs/index.md` table; `docs/development.md`; the TLS claim guard, its mutation tool and its test (+45 Python) |
| 829c636 | #589 | 2026-10-22 | The role move landed (plan only) | none |
| b10f2ac | #603 | 2026-10-26 | Phase 2: by hand | `manual.md` (407); `etc/systemd/` units (81); `etc/kerbside.conf.example`; `test_systemd_units.py` (120 Python); `installation.md` |
| d159d17 | #611 | 2026-10-27 | Phase 3: OpenStack | `kolla-ansible.md` (259); `use-cases/openstack.md` (+147 -85) |
| 46a03a6 | #617 | 2026-10-28 | Phase 4: containers | `containers.md` (158); `kolla-ansible.md` |
| c1f5938 | #631 | 2026-10-28 | Phase 5: Ansible | `ansible.md` (233); `ansible/README.md`; a workflow comment; `use-cases/shakenfist.md` |
| eebb1b2 | #635 | 2026-10-29 | Phase 6: installation hand-off | `installation.md`; `docs/index.md`; README, AGENTS.md, ARCHITECTURE.md |

Outside `docs/plans/` that is about 1,700 lines of documentation, 165
of Python (two test files and small changes to the guard), 81 of
systemd units, and a dozen of configuration, workflow comment and
collection README.

## What the survey found

1. **#589 is the plan's, and the Execution table does not record it.**
   `deployment-options-role-move-landed` changed only the master plan.
   It recorded that the role move had landed, which unblocked phase 5.
   It is not a phase, so it has no row. It belongs in the audit range
   all the same, because it changed the plan's own text. The planning
   commit records it in the master plan's new phase 7 paragraph.

2. **The master plan had no phase 7 paragraph.** It had only the
   shared block and a table row. The planning commit adds one, which
   names the range and #589.

3. **`tools/audit/plan-range.sh` refuses this range from a phase
   worktree.** It resolves the base branch by trying `develop` before
   `origin/develop` (`plan-range.sh:41-47`). The main clone has
   `develop` checked out at 7e8b8ea, behind `origin/develop`, and a
   worktree cannot fast-forward a branch another worktree has checked
   out. So the script reports "'eebb1b2' is not an ancestor of
   develop". That is loud, which is right, but it blames the SHA, not
   the stale ref. This predates the plan, so it is an issue, not a
   finding (step 7h).

4. **#491 is still open, so a single range would audit other plans'
   work.** `plan-range.sh` unions the files the merges touched, then
   diffs one contiguous range, `200d6b3^1..eebb1b2`, over them. Those
   18 days include the Proxmox source (#618 and #632) and other work
   on `docs/index.md`, `README.md`, `ARCHITECTURE.md`, `AGENTS.md`,
   `ansible/README.md`, `functional-tests.yml` and `installation.md`.
   The use-case-docs plan's audit met exactly this, reviewing 188
   unrelated lines of `test_db.py`.

5. **The plan's executable surface is small but real.**
   - Phase 1 extended the TLS claim guard's `DOC_PATHS` and its
     mutation tool, with a test.
   - Phase 2 shipped two systemd units and a 120-line test that holds
     them to the role's templates.
   - Phase 5 changed one workflow comment.

   The units are what an operator copies into `/etc`, so 2d has
   material, as do the operator instructions for secrets and TLS on
   the deployment pages.

6. **Review items no pull request took.** The automated reviews left
   `consider` items untaken. #635's two are worth 2c's attention as
   candidates, not findings:
   - item 2: the front-door default reads as contradicting the
     Kolla-Ansible option, which deploys Kerbside as part of OpenStack;
   - item 4: phase 6's title said "and closeout", which the phase did
     not do. The planning commit corrected that title at source.

7. **The master plan's *Dependencies on other plans* looks stale.**
   It still says shakenfist#4479 is open "on 2026-10-10". #589
   updated part of that section. 2c checks the rest against
   Shaken Fist's state on the day.

## Decisions

1. **Audit merge by merge, not as one range.** For each merge `M`, the
   range is `M^1..M`, with no path restriction. That is exactly one
   pull request's diff, which the shared block calls the whole of what
   landed. It sidesteps both #491 (finding 4) and the stale `develop`
   (finding 3) without changing the main clone. The mechanical scripts
   run seven times, once per merge, with `AUDIT_RANGE` exported and
   `AUDIT_PATHS` unset. The judgment agents read the seven diffs. The
   cost is seven tox runs of about 15 seconds each.

2. **Run all of wave 2, including 2d.** This is a documentation plan,
   but it shipped units an operator installs as root, a test that
   claims to hold them to the role, and instructions for handling the
   signing seed, database credentials, a Keystone password and TLS
   material. The use-case-docs audit ran 2d on a documentation plan,
   and it produced that phase's one substantive finding.

3. **2c runs on opus at medium, above the runbook's sonnet.** The
   documentation review is this plan's whole substance: six pages
   written over three weeks, and four shared blocks. The failure the
   shared block warns about is one phase's page made wrong by a later
   phase. Examples are a page written before #618 that lists source
   types without Proxmox, or a claim about client libraries stated
   differently on `manual.md`, `ansible.md` and `installation.md`.
   Finding those means holding all six pages at once. This is the
   decision a reviewer is most likely to argue with: the runbook
   names sonnet, and departing from it makes this audit harder to
   compare with others. I think the material justifies it. The other
   agents keep the runbook's settings.

4. **Findings are fixed in a follow-up pull request.** This phase's
   pull request is the record: the range, every wave's result, and
   every finding with its triage. Once it merges, a follow-up pull
   request fixes the blocking findings and the advisory ones worth
   taking. It also records this phase's merge commit in the `Merged`
   cell and marks phase 7 and the plan `Complete`. If the audit finds
   nothing to fix, this pull request marks both `Complete` itself, and
   no follow-up is needed.

5. **Keep the record public-safe.** The *Outcome* holds verdicts,
   paths, commands and counts. It quotes no configuration values,
   hostnames, addresses or credentials, even from examples. The
   definition of done runs a probe over the text this phase adds.

## Step plan

7a runs first and alone, because wave 2 is only worth spending on if
wave 1 passes. Steps 7b to 7f only read, so they spawn in parallel
once 7a is green. They write nothing to the worktree, only to their
own scratch directories. 7g depends on all of them. 7h is independent.

Every agent works from the *Audit range* table. "The seven diffs"
means `git diff M^1 M` for each merge `M` in it, in that order.
Unless a brief says otherwise, an agent reads every non-plan file,
and reads `docs/plans/` only for what the brief asks about.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7a | low | sonnet | none | Wave 1. For each merge `M` in the *Audit range* table, oldest first, run `AUDIT_RANGE="$M^1..$M" tools/audit/wave1.sh`, with `AUDIT_PATHS` unset. Report each run's exit code, and its advisory output verbatim, not only the fatal checks. Exit codes are at `PUSH-AUDIT.md:55-64`. Do not use `plan-range.sh` (decision 1), and do not change a range to make a run pass. If any run exits 6, stop and report. Expect fatal checks to find nothing in five merges that add no Python; say so per merge rather than omitting them. Report in under 300 words. |
| 7b | low | sonnet | none | Style conformance, the judgment portion. Execute the brief at `PUSH-AUDIT.md` "Style conformance — judgment portion" over the seven diffs, in place of `git diff develop...HEAD`. The conventions are in `AGENTS.md`. The material is the Python in merges 200d6b3 and b10f2ac: `tools/check-backend-tls-claims.py`, `tools/mutate-backend-tls-claims.py`, `kerbside/tests/unit/test_check_backend_tls_claims.py` and `kerbside/tests/unit/test_systemd_units.py`. Say "no material" for each checklist bullet that has none. Also apply the repository's Python rules: single quotes except docstrings, never triple single quotes, 120-column wrap outside `kerbside/` (check AGENTS.md for the limit inside it). Report in under 250 words. |
| 7c | medium | sonnet | none | Wave 2 mechanical, plus 2a code quality. Run `AUDIT_RANGE="$M^1..$M" tools/audit/wave2-mechanical.sh` once per merge, and report its output. It never exits non-zero on findings. Then execute the 2a brief in `PUSH-AUDIT.md` over the seven diffs, using that output as input. Its shared blocks bind: `python-version-discipline` (the floor is `requires-python = ">=3.11"`), `comment-proportion`, `source-file-size` and `plan-references-in-code`. The last also applies to the systemd units' and `etc/kerbside.conf.example`'s comments. Look hardest at `test_systemd_units.py`: does it hold the static units to the role's Jinja templates, or only to themselves? Report blocking or advisory per finding, with file and line, in under 400 words. |
| 7d | medium | sonnet | none | 2b test review over the seven diffs. The `functional-test-coverage` shared block binds. The material is `test_check_backend_tls_claims.py` (+28), against phase 1's `DOC_PATHS` change and the mutation tool's matching change, and `test_systemd_units.py` (120), against `etc/systemd/*.service` and `ansible/roles/kerbside/templates/`. Answer directly: would each test fail if the thing it guards regressed? For the units test, copy the tree to your scratch directory, change one directive in one static unit, and run the test there under `timeout 300`. Change one directive in the matching role template, and run it again. Never edit the worktree. Does `tools/mutate-backend-tls-claims.py` still have a mutation for `docs/deployment/*.md`? Report in under 350 words. |
| 7e | medium | opus | none | 2c documentation review over the seven diffs. Its four shared blocks bind (`readme-discipline`, `llm-doc-discipline`, `diagram-discipline`, `plan-phase-references`). Hold all six pages together: `docs/deployment/{manual,ansible,kolla-ansible,containers}.md`, `docs/installation.md` "Deploying for real", and `docs/use-cases/openstack.md` and `shakenfist.md` where the plan changed them. Look for a fact stated differently on two pages: which sources each mechanism configures, the client libraries (#602), the Keystone login and #629, TLS, the systemd units and who owns `kerbside.ini`, the CI cells. Check each against the index's Deployment Options table and against the code it describes, and say which is right. Look for pages written before the Proxmox source merged (#618, 2026-10-28) that list source types without it. Check the master plan's *Dependencies on other plans* and *Future work* against the state of shakenfist/shakenfist on the day, reading only: `gh pr view` and `gh issue view` for anything it names. Judge survey finding 6's #635 item 2. Grep `README.md` and `docs/`, excluding `docs/plans/`, for "phase <number>". Report blocking or advisory per finding, in under 500 words. |
| 7f | high | opus | none | 2d security review over the seven diffs. Its `path-traversal-review` shared block binds. The material is: `etc/systemd/kerbside-api.service` and `kerbside-daemon.service` (the user, the bind address, the hardening directives, how secrets reach the process); `etc/kerbside.conf.example`; every instruction in the deployment pages that handles a secret (the signing seed, database credentials, a Keystone service password, the `EnvironmentFile=` drop-in on `ansible.md` and `manual.md`) or TLS material; and every backend TLS claim, which must match `rust/kerbside-proxy/src/backend.rs` (`tools/check-backend-tls-claims.py` guards the wording, not the truth). An instruction that would leave a secret world-readable, in `systemctl show`, or in a log is a finding at the severity it would have in code. Also check that the guard's `DOC_PATHS` change cannot make the guard pass without reading files. Report severity, file, line, class and fix per finding, in under 500 words. |
| 7g | high | (management session) | n/a | Triage and record. Take 7a to 7f's reports. Reproduce every finding against the tree before accepting it. Classify each as blocking or advisory, decide fix, decline (with the reason) or file (if it predates the plan). Write it all into this plan's *Outcome*, per decision 5. Commit subject: "Record deployment options push audit." If nothing needs fixing, also mark phase 7 and the plan `Complete` in the master plan and `docs/plans/index.md`, with no `Merged` cell (decision 4). |
| 7h | low | (management session) | n/a | File one issue, "plan-range.sh trusts a stale local develop", once Mikal approves the wording. Body: the script prefers `develop` over `origin/develop` (`tools/audit/plan-range.sh:41-47`). In a worktree whose main clone has an old `develop` checked out, it rejects a current merge as "not an ancestor of develop". That blames the SHA rather than the ref, and the ref cannot be fast-forwarded from the worktree. Suggest preferring whichever of the two contains the other, or checking `develop` against `origin/develop` and saying which is stale. Link #491 as the other range defect. Record the URL in the execution notes. |

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| An agent audits the wrong content: a contiguous range, `develop...HEAD` (empty here), or a stale `develop`. | Decision 1, and every brief names the per-merge diffs. 7g checks that each 7a run's output names its range, and that the number of runs is seven. |
| 2c reports drift but cannot say which page is right. | 7e's brief requires a verdict checked against the code or the index. 7g reproduces each one. |
| A finding is real but predates the plan, and gets fixed here, widening the follow-up. | The push-audit rules: file it as an issue. 7g decides which with `git log -S` or blame against the seven merges. |
| The record quotes something that should not be public. | Decision 5, and the definition of done's probe. |
| The parallel agents trip over each other's tox runs. | Only 7a, alone, and 7c run tox, through the wave scripts. 7d runs only one test file, in a copy. |

## Definition of done

- [x] 7a ran `wave1.sh` seven times, once per merge in the *Audit
      range* table. The execution notes give each exit code.
- [x] 7c ran `wave2-mechanical.sh` seven times, likewise.
- [x] *Outcome* lists every finding from 7a to 7f, each with a
      verdict: fixed in the follow-up, declined (with the reason),
      or filed (with the issue). If there are none, it says so in
      one sentence.
- [x] Every finding *Outcome* accepts was reproduced against the
      tree by the management session.
- [x] The step 7h issue exists, or the execution notes record that
      Mikal declined it.
- [x] The private-data probe over the text this phase adds prints
      nothing else:
      `git diff origin/develop -- docs/plans/ | grep '^+' | grep -n -E '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}|\b([0-9]{1,3}\.){3}[0-9]{1,3}\b|stillhq|kasm|home\.|BEGIN [A-Z ]*PRIVATE KEY|password *[:=] *[^ <]'`.
      The probe's own line in this plan matches itself and is
      expected. Any other hit is either removed or shown to be a
      public example address.
- [x] Phase 7's status in the master plan and `docs/plans/index.md`
      matches decision 4: `In progress` if a follow-up is needed,
      otherwise `Complete` along with the plan.
- [x] `pre-commit run --all-files` passes.

## Outcome

Audited on 2026-10-30. Wave 1 and wave 2's mechanical script passed
on all seven merges. The judgment agents raised no critical or high
finding, and two blocking documentation findings. The management
session reproduced every finding it accepted below. The follow-up pull
request fixes the plan's own findings. Six problems that predate the
plan are filed as issues.

### Wave results

| Check | Result |
|-------|--------|
| 7a, `wave1.sh` per merge | Exit 0 on all seven. No advisory output. Its flake8 gate lints `HEAD~1..HEAD` rather than `AUDIT_RANGE`, so it saw no Python in any merge; that is #488, already open. The management session ran flake8 at the project's 120 columns over the plan's four Python files instead, and it is clean. |
| 7c, `wave2-mechanical.sh` per merge | Exit 0 on all seven. No TODO, `noqa`, broad except, assert, dependency or migration in any merge. |
| 7d, mutation of the plan's tests | 12 mutants. Every one was killed, except a comment change the units test ignores by design. |

### To fix in the follow-up pull request

| # | Source | Severity | Where | Finding | Introduced by |
|---|--------|----------|-------|---------|---------------|
| 1 | 7e | blocking | `docs/deployment/containers.md:15-20` | "Two mechanisms do exist and are documented" omits Ansible, whose page landed after it | 46a03a6, left stale by c1f5938 |
| 2 | 7e | blocking | `docs/deployment/ansible.md:228` | "The rootless test renders every source type" contradicts the same page's Proxmox row: `tools/ci-test-ansible-role.sh` renders no `proxmox` source | c1f5938 |
| 3 | 7f | medium | `docs/deployment/ansible.md:69-72`, `:231` | Advises a TLS proxy in front of the API without saying to bind the API to loopback (`kerbside_api_address`) or firewall it; the role binds every address by default, so tokens and Keystone passwords stay reachable in plain HTTP | c1f5938 |
| 4 | 7f | low | `docs/deployment/containers.md:118-126` | Says any `KERBSIDE_*` variable overrides a setting, without warning that a secret passed that way shows in `docker inspect` and the compose file | 46a03a6 |
| 5 | 7f | informational | `docs/deployment/manual.md:66` | Gives `/run/kerbside` systemd's default mode; `kerbside/rpc/server.py:47,53` forces 0700 on the directory and 0600 on the socket | b10f2ac |
| 6 | 7e | advisory | `docs/deployment/manual.md:77-79` | Names the oVirt driver's CA as the only temporary file; the Proxmox driver writes one too (`kerbside/sources/proxmox.py:212`) | b10f2ac, left stale by #618 |
| 7 | 7e | advisory | `docs/index.md:136-146`, `docs/installation.md:362-370` | Recommend a front-door Kerbside, then list Kolla-Ansible as a peer without saying it is the option that deploys Kerbside inside one cloud (#635's review, item 2). One clause each | 200d6b3, eebb1b2 |
| 8 | 7d, 7f | advisory | `kerbside/tests/unit/test_check_backend_tls_claims.py:229-230`, `tools/check-backend-tls-claims.py:127-132` | The docstring and the guard's comment still say no deployment page exists. Add a real-tree assertion that `docs/deployment/manual.md` is scanned. The guard's docstring also cites `backend.rs` lines that have moved; that predates the plan, but it is fixed in the same edit | 200d6b3 |
| 9 | 7c | advisory | `kerbside/tests/unit/test_systemd_units.py:33-36` | Use `jinja2.StrictUndefined`, so a template that starts using a variable from outside `defaults/main.yml` fails clearly instead of rendering it empty | b10f2ac |
| 10 | 7e | advisory | `docs/plans/PLAN-deployment-options.md`, *Dependencies on other plans* | Stale: shakenfist#4479 merged on 2026-10-18 and `PLAN-kerbside-deployer.md` is complete; v0.8.0 shipped; Shaken Fist's Kerbside lane moved to a weekly run (shakenfist#4653) | 200d6b3 |

### Filed, because they predate the plan

- #639: AGENTS.md says to wrap at 80 columns, but the project lints at
  120, and the tree does not follow 80 (7b).
- #640: the example units and the role's unit templates have no
  sandboxing. `systemd-analyze security` rates both 9.0 (7f). The
  static units copy templates that predate the plan.
- #641: `check-backend-tls-claims.py` passes when its globs match no
  files (7f).
- #642: the Rust proxy inherits every `kerbside.ini` secret through its
  environment (7f).
- #643: `ci-test-ansible-role.sh` calls `multi.json` "one source of
  every type", but it has no `proxmox` source (7e).
- #644: `use-cases/multi-cloud.md` cites
  `tools/sf-e2e/deploy-kerbside.sh`, which #587 deleted (7e).

### Declined, and why

- `tools/mutate-backend-tls-claims.py:32` is 80 columns (7b). The
  project lints at 120; the disagreement with AGENTS.md is #639.
- Add `encoding=` to `test_systemd_units.py`'s two `open()` calls (7c).
  Repository practice is the opposite: 36 test `open()` calls omit it,
  and 2 pass it.
- `test_api_binds_loopback` overlaps the overrides pin, and the units
  test skips outside a checkout (7c). Both are deliberate and
  documented.
- The units test never renders the template's IPv6 bracketing (7d). Its
  job is to hold the example units, which bind loopback over IPv4, to
  the templates. The bracketing is the role's own logic, which predates
  the plan.
- `manual.md`'s SQL puts the database password in the MariaDB client's
  history (7f). The page shows a `CHANGEME` placeholder in a one-off
  setup statement. Where an operator's client keeps history is their
  environment's concern, and the same pattern runs through every
  MariaDB setup guide.
- The master plan's *Prompt* names `tools/sf-e2e/deploy-kerbside.sh`,
  since deleted (7e). The Prompt records what was asked on the day; it
  is history, not a claim about the tree.

## Execution notes

- Back brief answered on 2026-10-30 (AEDT). Mikal kept decision 3
  (2c on opus) and decision 4 (fixes in a follow-up pull request),
  approved step 7h's issue as worded, and gave this session permission
  to push and open the pull request once the definition of done
  passes.
- Step 7h ran first, beside 7a: #638,
  https://github.com/shakenfist/kerbside/issues/638
- 7a found that wave 1's flake8 gate ignores `AUDIT_RANGE`. That is
  #488, still open, so it is not a new finding. The management session
  ran flake8 over the plan's Python directly; the *Outcome* records it.
- 7b to 7f ran in parallel once 7a passed. None of them wrote to the
  worktree.
- Definition of done checked on 2026-10-30. The *Execution notes* and
  the step logs in the session's scratch directory show seven runs each
  of `wave1.sh` and `wave2-mechanical.sh`. The management session
  reproduced each of the *Outcome*'s ten items against the tree, and
  confirmed shakenfist#4479 and #4653 merged with `gh pr view`. The
  private-data probe matched only its own line in this plan.
  `pre-commit run --all-files` passed.
- Phase 7 stays `In progress`. The follow-up pull request fixes the
  *Outcome*'s ten items, records this phase's merge commit, and marks
  phase 7 and the plan `Complete`.

## Back brief

Before executing any step of this plan, back brief the operator
on your understanding of the plan and how the work you intend to
do aligns with it. Three points need Mikal's answer before 7a runs:

- **Decision 3:** 2c on opus, above the runbook's sonnet.
- **Decision 4:** fixes in a follow-up pull request, not this one.
- **Step 7h's issue wording**, which is published.
