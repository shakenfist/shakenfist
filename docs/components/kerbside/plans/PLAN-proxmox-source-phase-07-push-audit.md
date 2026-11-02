# Proxmox source phase 7: push audit

Master plan: [PLAN-proxmox-source.md](/components/kerbside/plans/PLAN-proxmox-source/).

## Planning effort

This plan was written in a high-effort session. The master plan gives
the phase no effort level, only the push-audit shared block. Review
effort: medium. The judgment is in the range and in how the review is
split, not in the steps, which follow `PUSH-AUDIT.md`.

## Scope

In scope:

- Running `PUSH-AUDIT.md`, waves 1 and 2, over everything the plan
  landed in this repository. That is five merges carrying code, listed
  under *Audit range*.
- The shared block's floor checklist, items 2 to 5, which this plan's
  blast radius makes concrete. It added:
  - a scheduled workflow;
  - live console passwords and a credential-bearing CONNECT host held
    in the shared database;
  - a `Target` proto change that feeds the proxy wheel's published dev
    releases;
  - an oVirt ticket column that stopped being written.
- Triaging every finding as blocking or advisory, with the reason, in
  this plan's *Outcome*.
- Filing problems that predate the plan as issues.

Out of scope:

- Fixing findings. They land in a follow-up pull request after this one
  merges (decision 5).
- Phases 1b and 2, which landed in shakenfist/actions and ryll and were
  audited there (survey finding 2).
- The open audit-tooling defects #488, #491 and #638. This plan works
  around them (decision 1) and does not fix them.
- Deployment options' follow-up pull request, which is not yet open. It
  also touches the TLS claims checker (survey finding 6).

## Audit range

Every merge below is a merge commit on `develop`'s first-parent chain,
and its diff against its first parent is the whole of what that pull
request landed. Non-plan lines are additions outside `docs/plans/`.

| Merge | PR | Date | Phase | Non-plan content |
|-------|----|------|-------|------------------|
| 92e7b8a | #458 | 2026-09-20 | Feasibility note | none: `docs/plans/` only |
| 8bb9059 | #476 | 2026-09-24 | Plan revisions, phases 1b and 2 planned | none |
| 353eda9 | #506 | 2026-09-28 | Phase 3a planned | none |
| 696f5aa | #604 | 2026-10-25 | 3a. Minting at connect time | about 3,480 lines: `kerbside/sources/minting.py` (171), `ovirt.py`, `db.py`, `api.py`, `rpc/servicer.py`, migration `d019b0e3c87a`, the direct-qemu ticket tools (`qmp-client.py`, `verify-ticket-rotation.sh`), `tools/ovirt-e2e/drive-console.py` (+297), and nine docs pages |
| c435ea3 | #610 | 2026-10-28 | 3b. Tunnel transport | about 1,810 lines: `rust/kerbside-proxy/src/backend.rs` (+232 -76) and `session.rs` (+172), `kerbside.proto` and the generated stubs, migration `cfdafafd3044`, `db.py`, `servicer.py`, `tools/direct-qemu/connect-proxy.py` and `run-tunnel-checks.sh` |
| 6519c3b | #618 | 2026-10-28 | 4. The source driver | about 2,060 lines: `kerbside/sources/proxmox.py` (418), its test (785), migration `dabe2f66e369`, the direct `.vv` refusal in `api.py`, the ansible role's source checker and argument specs, `console-sources.md` (+138) |
| c4b4011 | #632 | 2026-10-29 | 5. CI lane | about 4,940 lines: `.github/workflows/proxmox-functional.yml` (311), `tools/proxmox-e2e/` (six scripts, 2,610 lines including `drive-console.py` at 1,484), their test (1,581), `backend.rs` and `relay.rs` (+18) |
| 746c7ef | #647 | 2026-10-29 | 6. Docs | about 450 lines: `docs/use-cases/proxmox.md` (349), the sibling pages, the TLS claims checker's docstring and two tests |

The phase merges are the master plan's `Merged` cells. The three
plan-only merges were found with `git log --merges --first-parent
origin/develop` on branch names containing `proxmox`. They are listed
for completeness: the shared block counts a change to the plan's own
text as the plan's work.

## What the survey found

1. **The master plan's phase 7 row prescribes `plan-range.sh`, which
   would audit the wrong content.** The row says to derive the range
   from the `Merged` cells with `tools/audit/plan-range.sh`. Three
   open issues stand against that:
   - #491: the script unions the touched files and diffs one
     contiguous range, here `696f5aa^1..746c7ef`, which spans five
     days of other plans' work on `docs/index.md`, `README.md`,
     `docs/installation.md`, `docs/testing.md` and more.
   - #638: from a phase worktree, the script prefers the main clone's
     stale `develop`, which is 13 commits behind today.
   - #488: wave 1's flake8 gate ignores `AUDIT_RANGE`.

   Deployment options' push audit met all three on 2026-10-30 and
   audited merge by merge instead. The planning commit corrects the
   row at its source.

2. **Phases 1b and 2 were audited in their own repositories, as the
   shared block requires.** Phase 1b's actions half was audited by its
   step 1b.6, recorded in that plan's *Outcome*. Its ryll half, and
   phase 2, were audited by ryll's `PUSH-AUDIT.md` in phase 2's step
   2g, and the findings landed as ryll #411. Phase 1 changed no code.
   This audit cites those and does not repeat them.

3. **The plan changed Rust, and `PUSH-AUDIT.md` has no Rust gate.**
   Wave 1 is tox only. Phase 3b rewrote the backend dial in
   `backend.rs` and added `session.rs` (about 400 lines), and phase 5
   touched `backend.rs` and `relay.rs`. `rust.yml` runs `cargo fmt
   --check`, `cargo clippy --all-targets -- -D warnings` and `cargo
   test` on every push to `develop` that touches `rust/**`, so it has
   a run on each merge's exact tree. 7a cites those runs (decision 2).

4. **This plan's security surface is large, so 2d is split in two.**
   What the plan put into production:
   - 3a put live SPICE passwords on the session token row in the
     shared database, for about a minute and a half, under a row lock.
     The master plan meant to keep them out of it.
   - 3b put the signed CONNECT host, which 3b's own survey calls a
     credential, beside the ticket.
   - 4 added a driver that writes the cluster CA to a temporary file,
     authenticates with an API token, and mints. Every mint replaces
     the VM's SPICE password.
   - 5 added a scheduled workflow that deploys a node, creates tokens
     with real secrets, and drives sessions on a self-hosted runner. Its
     round-3 review found raw log tails reaching the public job log
     before the secret scan ran.

   The deployment-options audit's one 2d agent covered about 1,700
   lines of documentation. This plan's 2d material is about 9,000
   lines of code and a workflow.

5. **Phase 6 left a known gap in the TLS claims checker.** A sentence
   is a claim only if it names the backend: `BACKEND` in
   `tools/check-backend-tls-claims.py` lists backend, hypervisor,
   node and target. The page's own heading, "The SPICE session to qemu
   is TLS and pinned", names none of them, so the checker no longer
   reads it as a claim. The bullet states its conditions, so the page
   is right today. A later edit that removed them would pass. This is
   recorded in #647's last commit message and the index, and nowhere
   else. It is a candidate finding for 7e, introduced by 746c7ef. The
   phase 6 decision to leave the rules unchanged was taken before the
   gap was known.

6. **Deployment options' follow-up overlaps this audit's likely
   fixes.** That audit's item 8 changes
   `tools/check-backend-tls-claims.py` and its test. Phase 6 already
   refreshed the docstring's `backend.rs` line references, which is
   half of that item. #641 (the checker passes when it reads no files)
   is open against the same file. Whichever follow-up opens second
   rebases onto the first; 7g notes the overlap.

7. **Deferred work across the plan should be checked, not assumed.**
   Phase 3a recorded serialising mints across daemons as future work.
   Phase 5 recorded that the action's own token is not least-privilege.
   Phase 6 recorded the worked example's follow-up. 7e checks that each
   *Future work* entry in the phase plans has an issue, or is still
   tracked somewhere live. The shared block counts silent
   disappearance as a failure.

8. **The master plan's Status stated the overclaim phase 6 corrected.**
   It said the proxy "dials a minted tunnel over TLS only". The
   close-out commit before this plan corrects it at source. Line 163's
   account, a transport step beneath TLS, was already right.

## Decisions

1. **Audit merge by merge.** For each code merge `M`, the range is
   `M^1..M` with `AUDIT_PATHS` unset. That is exactly one pull
   request's diff, it sidesteps #491 and #638, and it has precedent:
   the deployment-options audit used it. The cost is five tox runs. The
   shared block's real point is what the phases did to each other, so
   the judgment agents read all five diffs together, and each brief
   names a cross-phase question (decision 4). The three plan-only
   merges are not run through the wave scripts. They would pass
   vacuously, and 7e reads the master plan's final text.

2. **Rust is gated by CI's own runs, not by a new local gate.** 7a
   finds `rust.yml`'s push run on c435ea3 and c4b4011 with the
   read-only `ci-status` helper and records each conclusion and run
   ID. If either run is missing or not a success, 7a runs the same
   three cargo commands in Docker against a checkout of that merge, as
   the repository's Rust work always does. 7b and 7c read the Rust
   diff for judgment.

3. **2d is two agents, both opus at high effort.** 7f1 takes the
   ticket and tunnel path: 3a and 3b. 7f2 takes the driver and the
   lane: 4 and 5. Each holds one coherent threat model, and neither
   has to hold 9,000 lines at once. The runbook names one 2d agent,
   and this is the decision a reviewer is most likely to argue with.
   Splitting risks missing an issue that crosses the boundary, such as
   a ticket that 3a stores and 5's diagnostics print. So both briefs
   name the handoff: 7f1 states what is sensitive and where it lives,
   and 7f2 checks every path in 4 and 5 that touches those fields. 2c
   runs on opus at medium, as in the deployment-options audit, because
   the docs material spans nine pages written over five weeks, and
   drift between them is the failure the shared block names.

4. **Each judgment brief carries one cross-phase question.** These are
   questions per-merge reading cannot answer:
   - 7c: does `tools/proxmox-e2e/drive-console.py` duplicate
     `tools/ovirt-e2e/drive-console.py`, which 3a grew by 297 lines?
   - 7d: do 3a's minting tests still hold for a second driver?
   - 7e: do docs written in 3a about the oVirt ticket still hold after
     3b and 4?
   - 7f1 and 7f2: the handoff in decision 3.

5. **Findings are fixed in a follow-up pull request.** This phase's pull
   request is the record: the range, every wave's result, and every
   finding with its triage. Once it merges, a follow-up pull request
   fixes the blocking findings and the advisory ones worth taking.
   It also records this phase's merge commit, and marks phase 7 and
   the plan `Complete`. If the audit finds nothing to fix, this pull
   request marks both `Complete` itself.

6. **Keep the record public-safe.** The *Outcome* holds verdicts, paths,
   commands and counts. It quotes no ticket, token, CONNECT host,
   hostname or address, even from a test fixture. The definition of
   done runs a probe over the text this phase adds. That matters more
   here than for a documentation plan, because the material is full of
   credential-shaped fixtures.

## Step plan

7a runs first and alone, because wave 2 is only worth spending on if
wave 1 passes. Steps 7b to 7f2 only read, so they spawn in parallel
once 7a is green. Each writes only to its own scratch directory. 7g
depends on all of them.

Every agent works from the *Audit range* table. "The five diffs" means
`git diff M^1 M` for each code merge `M`, oldest first. An agent reads
`docs/plans/` only for what its brief asks about. Mutation runs happen
on copies, never on the worktree, one per run under `timeout 300`, and
a timeout counts as killed. No agent prints a secret, ticket, CONNECT
host or token value from a fixture or a log. Each reports names and
counts only.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7a | low | sonnet | none | Wave 1. For each of the five code merges `M`, oldest first, run `AUDIT_RANGE="$M^1..$M" tools/audit/wave1.sh` with `AUDIT_PATHS` unset. Report each run's exit code and its advisory output. Exit codes are in `PUSH-AUDIT.md` under Wave 1. Do not use `plan-range.sh` (decision 1), and never change a range to make a run pass. If any run exits 6, stop and report. The flake8 gate ignores `AUDIT_RANGE` (#488), so also run `.tox/flake8/bin/flake8 --max-line-length 120` over the Python files each merge added or changed that still exist, and report per merge. Then, for Rust (decision 2): find `rust.yml`'s push run on c435ea3 and on c4b4011 with `$HOME/bin/ci-status shakenfist/kerbside runs --branch develop --limit 100`, and report each conclusion and run ID. If either is missing or failed, run `cargo fmt --all --check`, `cargo clippy --all-targets -- -D warnings` and `cargo test` in Docker against a `git worktree add --detach` of that merge in your scratch directory, the way `rust.yml` builds, and remove the worktree afterwards. Report in under 350 words. |
| 7b | low | sonnet | none | Style conformance, the judgment portion. Execute `PUSH-AUDIT.md`'s "Style conformance — judgment portion" brief over the five diffs, in place of `git diff develop...HEAD`. The conventions are in `AGENTS.md`. The material is the Python in all five, the Rust in c435ea3 and c4b4011, and the shell in `tools/direct-qemu/` and `tools/proxmox-e2e/`. Look hardest at the bullets on source backends (`proxmox.py` and `minting.py` against `sources/base.py`'s contract), audit events, config, logging and migrations (three new revisions, each needing both upgrade and downgrade). Apply the repository's Python rules: single quotes except docstrings, no triple single quotes, 120 columns. Say "no material" for any bullet with none. Report in under 300 words. |
| 7c | medium | sonnet | none | Wave 2 mechanical, plus 2a code quality. Run `AUDIT_RANGE="$M^1..$M" tools/audit/wave2-mechanical.sh` once per code merge, and report its output; it never exits non-zero on findings. Then execute the 2a brief in `PUSH-AUDIT.md` over the five diffs, with that output as input. Its shared blocks bind: `python-version-discipline` (the floor is `pyproject.toml`'s `requires-python`), `comment-proportion`, `source-file-size` and `plan-references-in-code`. The size block has material: `tools/proxmox-e2e/drive-console.py` (1,484), `kerbside/tests/unit/test_proxmox_e2e_tools.py` (1,581) and `test_sources_proxmox.py` (785). Answer the cross-phase question (decision 4): how much of `tools/proxmox-e2e/drive-console.py` duplicates `tools/ovirt-e2e/drive-console.py`, and is there a seam a shared module could take? Check the floor checklist's item 4: 3a stopped writing the oVirt ticket to the `Console` row, and 3b changed `Target`. Grep this repository, `../ryll` if present, and kerbside-patches if present for any consumer left reading what was removed or renamed, and state the greps. Report blocking or advisory per finding, with file and line, in under 450 words. |
| 7d | medium | sonnet | none | 2b test review over the five diffs. The `functional-test-coverage` shared block binds. Answer directly whether each guard below would fail if what it guards regressed, by mutating a copy of the tree in your scratch directory and running only the relevant test file under `timeout 300`: the session ticket row lock (`minting.py`, `test_sources_minting.py`, and `test_session_ticket_lock.py`, which needs MariaDB, so say so and skip it if none is available); the per-console guard against an abandoned mint replacing a newer ticket; the tunnelled target missing a field, which must be a clean denial (`servicer.py`, `test_rpc.py`); the Proxmox console uuid of vmid plus SMBIOS UUID; a failed read making the source errored rather than deleting its consoles (`proxmox.py`); the direct `.vv` refusal for a proxied-only source (`api.py`); and the privilege-necessity logic in `tools/proxmox-e2e/check-privileges.py`. Answer the cross-phase question (decision 4): which of 3a's minting tests are oVirt-specific, and does the Proxmox driver have equivalents? Keep the mutations in a script in your scratch directory and give its path. Report in under 400 words. |
| 7e | medium | opus | none | 2c documentation review over the five diffs. Its four shared blocks bind (`readme-discipline`, `llm-doc-discipline`, `diagram-discipline`, `plan-phase-references`). Hold the pages together: `docs/console-sources.md` (the oVirt and Proxmox sections), `docs/use-cases/proxmox.md` and `ovirt.md`, `docs/proxy-architecture.md`, `docs/schema.md`, `docs/network-ports.md`, `docs/testing.md`, `docs/configuration.md`, `tools/proxmox-e2e/README.md` and `tools/ovirt-e2e/README.md`. Look for a fact stated differently on two pages: ticket lifetime and reuse, where the ticket is stored and for how long, what a mint does to a connected client, the tunnel's TLS shape, privileges, ports and the lane's tier. Check each against the code and say which is right. Answer the cross-phase question (decision 4): does anything 3a wrote about the oVirt ticket no longer hold after 3b and 4? Judge survey finding 5, the checker's blind spot: is it a finding against 746c7ef, and what is the smallest fix (a `BACKEND` word, or a different heading)? Check every *Future work* entry in the seven phase plans for an issue, or somewhere live that still tracks it (survey finding 7), reading GitHub only with `gh issue view` and `gh pr view`. Grep `README.md`, `AGENTS.md`, `ARCHITECTURE.md` and `docs/` outside `docs/plans/` for "phase <number>". Report blocking or advisory per finding, in under 550 words. |
| 7f1 | high | opus | none | 2d security review of the ticket and tunnel path: the diffs of 696f5aa and c435ea3. Its `path-traversal-review` shared block binds. The material is: the session token row's ticket and tunnel columns (`db.py`, migrations `d019b0e3c87a` and `cfdafafd3044`), their lifetime, reaping and every reader; `minting.py`'s row lock and abandoned-mint guard under concurrent daemons; `servicer.py`'s authorize path and what it returns to the proxy; `backend.rs` and `session.rs`: the tunnel dial, the refusal without a host subject, the CA mapping, and whether any log line, error string or audit event carries a ticket or CONNECT host; the proto change and `kerbside/rpc/contract.py`. Check the floor checklist's item 5: the proto change moves the contract hash that `PLAN-proxy-dev-releases.md`'s published dev wheels depend on. Does a mismatched daemon and proxy fail closed? For the handoff (decision 3), end your report with a list of every field and value that is sensitive and where it is stored, so 7f2's results can be checked against it. Report severity, file, line, class and fix per finding, in under 550 words. |
| 7f2 | high | opus | none | 2d security review of the driver and the lane: the diffs of 6519c3b and c4b4011. Its `path-traversal-review` shared block binds. The material is: `kerbside/sources/proxmox.py` (the token in request headers, the CA temporary file's mode, path and cleanup, TLS verification of the API, what an error or log line includes, and the `proxy` parameter that names the CONNECT target); the direct `.vv` refusal in `api.py`; `ansible/roles/kerbside/files/kerbside-check-sources.py`; and `.github/workflows/proxmox-functional.yml` with `tools/proxmox-e2e/`. For the lane, check: the fork guard on `pull_request`; which steps see a secret; whether any token secret, ticket, CONNECT host or node address can reach the public job log, an uploaded artifact or a filed issue, especially on the failure paths that round 3 of #632's review fixed; and SSH host-key handling to the node. Apply the floor checklist's items 2 and 3: does the nightly run report its own failure somewhere a human sees, and did any secret, private hostname or address enter the repository? For the handoff (decision 3), check every path in 4 and 5 that reads the ticket or tunnel fields 3a and 3b added (`db.py`'s session token row). Report severity, file, line, class and fix per finding, in under 550 words. |
| 7g | high | (management session) | n/a | Triage and record. Take 7a to 7f2's reports. Reproduce every finding against the tree before accepting it. Check 7f2's results against 7f1's sensitive-field list. Classify each finding as blocking or advisory, and decide: fix, decline (with the reason), or file (if it predates the plan, using `git log -S` or blame against the five merges). File the issues for findings that predate the plan, with wording shown to Mikal first. Write it all into this plan's *Outcome*, per decision 6. Commit subject: "Record the Proxmox source push audit." If nothing needs fixing, also mark phase 7 and the plan `Complete` in the master plan and `docs/plans/index.md`, with no `Merged` cell (decision 5). |

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| An agent audits the wrong content: a contiguous range, `develop...HEAD` (empty here), or a stale `develop`. | Decision 1, and every brief names the per-merge diffs. 7g checks that each 7a run's output names its range, and that there are five runs. |
| The split 2d misses a defect that crosses its boundary. | Decision 3's handoff. 7f1 lists the sensitive fields, 7f2 checks the paths that read them, and 7g checks one against the other. |
| An agent copies a credential-shaped fixture into its report, and from there into the plan. | Every brief forbids printing values, and decision 6's probe runs over the text this phase adds before the commit. |
| A mutation run damages the worktree, or runs forever. | Mutations run on copies, one per run under `timeout 300`. 7g checks `git status --short` is empty before committing. |
| The parallel agents trip over each other's tox runs. | Only 7a, running alone, and 7c run tox, through the wave scripts. 7d runs single test files in a copy. |
| A finding is real but predates the plan, and gets fixed here, widening the follow-up. | The push-audit rules: file it as an issue. 7g decides which with `git log -S` or blame against the five merges. |
| The follow-up collides with deployment options' follow-up in the TLS checker. | Survey finding 6. 7g names the overlap in the *Outcome*, and the second follow-up to open rebases. |

## Definition of done

- [x] 7a ran `wave1.sh` five times, once per code merge. The execution
      notes give each exit code and the per-merge flake8 result.
- [x] 7a recorded `rust.yml`'s conclusion and run ID for c435ea3 and
      c4b4011, or the result of the Docker cargo run that replaced a
      missing one.
- [x] 7c ran `wave2-mechanical.sh` five times, likewise.
- [x] *Outcome* lists every finding from 7a to 7f2, each with a
      verdict: fixed in the follow-up, declined (with the reason), or
      filed (with the issue). If there are none, it says so in one
      sentence.
- [x] Every finding *Outcome* accepts was reproduced against the tree
      by the management session.
- [x] *Outcome* gives a verdict on each of the floor checklist's items
      2 to 5, and on survey finding 5.
- [x] The private-data probe over the text this phase adds prints
      nothing else:
      `git diff origin/develop -- docs/plans/ | grep '^+' | grep -n -E '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}|\b([0-9]{1,3}\.){3}[0-9]{1,3}\b|stillhq|kasm|home\.|pvespiceproxy:|PVEAPIToken=|BEGIN [A-Z ]*PRIVATE KEY|password *[:=] *[^ <]'`.
      The probe's own line in this plan matches itself and is
      expected. Any other hit is removed, or shown to be a public
      example address.
- [x] `git status --short` in the worktree is empty after every agent
      has finished.
- [x] Phase 7's status in the master plan and `docs/plans/index.md`
      matches decision 5: `In progress` if a follow-up is needed,
      otherwise `Complete` along with the plan.
- [x] `pre-commit run --all-files` passes.

## Bugs fixed during this work

None yet.

## Future work

None yet.

## Execution notes

- 2026-10-30 AEDT, back brief. Mikal kept decision 3 (2d split into two
  opus agents at high effort, and 2c on opus) and decision 5 (fixes in a
  follow-up pull request). He gave this session permission to push and
  open the pull request once the definition of done passes, and to file
  the issues for findings that predate the plan after showing him their
  wording.
- 7a ran alone. `wave1.sh` exited 3 on 696f5aa, 6519c3b and c4b4011,
  and 0 on c435ea3 and 746c7ef. The cause is the same everywhere:
  `print()` in seven files that carry no `audit-allow-print` marker.
  Those files are `tools/direct-qemu/qmp-client.py`, the migrations
  `d019b0e3c87a` and `dabe2f66e369`, and four `tools/proxmox-e2e/`
  scripts. Every one of these prints is deliberate output: CLI tools,
  or migrations telling the operator what they changed. None is
  service code bypassing logging. `PUSH-AUDIT.md` says to fix a wave 1
  failure before wave 2, but decision 5 puts fixes in the follow-up.
  So the management session reproduced the cause, recorded it as
  finding 5 below, and started wave 2 rather than blocking on it.
  Because the script stops at that check, wave 1's advisory long-line
  and whitespace checks did not run on those three merges. flake8 at
  120 columns, run directly over each merge's Python, is clean, and
  pre-commit enforces whitespace.
- 7a's per-merge flake8 used `.tox/shared/bin/flake8`, because the
  flake8 environment has no binary of its own here. The generated
  `kerbside_pb2*.py` stubs were excluded.
- `rust.yml` passed on both merges that touched Rust: run 40628778333
  on c435ea3 and run 40842114830 on c4b4011 (fmt, clippy with
  `-D warnings`, test and build). The Docker fallback was not needed.
- 7b to 7f2 ran in parallel once 7a was done. None wrote to the
  worktree, and `git status --short` was empty after every one.
- 7c ran `wave2-mechanical.sh` once per code merge, and all five
  exited 0.
- 7d ran 28 mutants on a copy of the tree, and 27 were killed. The
  survivor removes the session ticket's `FOR UPDATE`, which SQLite
  ignores. The only guard is `test_session_ticket_lock.py` on MariaDB,
  which the direct-qemu lane runs through
  `tools/direct-qemu/run-ticket-lock-test.sh`. This host has no
  MariaDB, so it was not run here.
- 7f2 found no Proxmox nightly run at 07:28 UTC. The management session
  checked the workflow's run list: its eight runs are all pull request
  runs. Every scheduled workflow in this repository fires about six
  hours after its cron time. The 04:30 functional run started at 10:58
  on 2026-10-29. So the first nightly, due 05:30 UTC on 2026-10-30, had
  not yet fired. The follow-up pull request confirms that it ran.
- The first scheduled Proxmox nightly ran at 11:31 UTC on 2026-10-30,
  run 41048695421, and passed.
- 2026-10-31 AEDT, the follow-up. Mikal asked for it once #662 merged
  as d2824dd. *Outcome*'s *Resolution* records the fixes.
- 7g filed the issues Mikal approved, with the wording he was shown, on
  2026-10-30 AEDT:
  - shakenfist/kerbside #656, #657, #658, #659, #660 and #661;
  - a comment on #138;
  - shakenfist/ryll #665 and #666;
  - shakenfist/actions #216.

## Outcome

Audited on 2026-10-30. The judgment agents raised no critical, high or
medium finding. Four low security findings and nine advisory ones are
in the plan's own diff, and go to the follow-up pull request. The
management session reproduced every accepted finding against the tree.
Eight *Future work* entries that no issue tracked, and four problems
that predate the plan, are filed. The follow-up pull request fixed
eleven and declined one, and the plan is complete (*Resolution*).

### Wave results

| Check | Result |
|-------|--------|
| 7a, `wave1.sh` per code merge | Exit 3 on 696f5aa, 6519c3b and c4b4011, all from finding 5; exit 0 on c435ea3 and 746c7ef. flake8 and the unit suite pass on all five |
| 7a, Rust | `rust.yml` passed on c435ea3 (40628778333) and c4b4011 (40842114830) |
| 7c, `wave2-mechanical.sh` per code merge | Exit 0 on all five. No new TODO, `noqa`, assert or dependency. Three migrations, each with upgrade and downgrade. Eight broad excepts in 696f5aa, each of which logs a class name and re-raises or records the error |
| 7d, mutation | 28 mutants, 27 killed. The survivor is the row lock, which only the MariaDB test in the direct-qemu lane guards (finding 11) |

### Floor checklist

- **Item 1, landings:** every phase is a merge commit read against its
  first parent. Phases 1b and 2 cite their audits in actions and ryll.
  Phase 1 changed no code.
- **Item 2, unattended work:** met. The nightly lane files or updates a
  `Nightly proxmox lane failing` issue when it fails, and the issue
  carries only the run URL (`proxmox-functional.yml`'s issue job). A
  cancelled or never-started run files nothing, as for every other
  lane. Its first scheduled run had not fired at audit time (*Execution
  notes*).
- **Item 3, secrets and addresses:** pass. The credential-shaped test
  fixtures are short sentinels with `example.org` hosts. The only
  addresses are loopback, or the public action's RFC 1918 default.
- **Item 4, removed or renamed:** no consumer is left. 3a stopped
  writing the oVirt ticket to the `Console` row and removed
  `store_console_ticket` and `acquire_ticket`. 3b changed `Target`.
  Greps over this repository, ryll at e49ddce and kerbside-patches at
  b81cd62bd found no remaining caller outside `docs/plans/`.
- **Item 5, publishing:** fails closed. A daemon and proxy built
  against different `kerbside.proto` contracts refuse to start
  (`proxy_supervisor.check_contract`). Even with that check skipped, an
  old proxy ignores the tunnel field and dials port 0, which fails.

### To fix in the follow-up pull request

| # | Source | Severity | Where | Finding | Introduced by |
|---|--------|----------|-------|---------|---------------|
| 1 | 7f2 | low | `kerbside/sources/proxmox.py`, url handling; `ansible/roles/kerbside/files/kerbside-check-sources.py` | An `http://` url is accepted, so the API token travels in cleartext and the CA check never runs. Refuse any scheme but `https`, in the driver and the role's checker, and say so in `console-sources.md`'s table | 6519c3b |
| 2 | 7f2 | low | `kerbside/sources/proxmox.py`, the mint's return | The tunnel's proxy URI comes from `spiceproxy`'s reply unchecked, and the connect host is then sent to it in plaintext. Require its host to equal the source's API host. `console-sources.md` should also say that the 3128 leg carries the connect host in the clear, as the use-case page does | 6519c3b |
| 3 | 7f1 | low | `kerbside/sources/minting.py`, worker registration | The worker is registered before `thread.start()`. If the start raises, an unstarted thread stays registered, and every later mint for that console fails to join it for `MINT_STUCK_AFTER` (300 s). Unregister it, under the lock, if `start()` raises | 696f5aa |
| 4 | 7e | advisory | `tools/check-backend-tls-claims.py` `BACKEND`; `docs/use-cases/proxmox.md` value bullet | The checker does not read the page's heading, "The SPICE session to qemu is TLS and pinned", as a claim, so removing the bullet's conditions would pass. Add `qemu` to `BACKEND`, with a test. The real docs still pass with it (checked on a copy). Overlaps deployment options' follow-up item 8 and #641, so whichever opens second rebases | 746c7ef |
| 5 | 7a | advisory | the seven files in *Execution notes* | Deliberate output without the `audit-allow-print` marker that `tools/check-pypi-storage.py` and the other reporting CLIs carry. Add the marker | 696f5aa, 6519c3b, c4b4011 |
| 6 | 7e | advisory | `docs/network-ports.md`, the firewall summary | Lists "every hypervisor's plaintext and TLS SPICE ports" for Kerbside's outbound traffic, without the Proxmox API host's 3128, which the same page's table and the use-case page both require | 6519c3b |
| 7 | 7e | advisory | `docs/proxy-architecture.md`, the minting sequence | Widened to "oVirt and Proxmox VE", but step 1's requested lifetime is ignored by Proxmox, and step 3's abandoned-worker risk is narrowed for Proxmox by `MINT_POST_CUTOFF`, which no doc mentions | 6519c3b |
| 8 | 7e | advisory | `ARCHITECTURE.md`, the Proxmox VE source row | Restates the 30-second lifetime and the 409 refusal, which `console-sources.md` owns. Cut to a summary and the link (`llm-doc-discipline`) | 6519c3b |
| 9 | 7f1 | advisory | `docs/schema.md`, the session token's backend columns | Add one sentence: these columns hold live, short-lived credentials, which therefore also reach binlogs, replicas and backups | 696f5aa, c435ea3 |
| 10 | 7f2 | advisory | `tools/proxmox-e2e/drive-console.py`, `Lane.remember` | Also emit `::add-mask::` for each remembered value under Actions, so that output which bypasses the redaction is masked once a value is known. (Corrected in the follow-up: this does not cover the window before the session's ticket is captured, since a mask registered later cannot hide earlier output. That window stays covered by Kerbside never logging a ticket, which 7f1 checked) | c4b4011 |
| 11 | 7d | advisory | `kerbside/tests/unit/test_db.py` | A unit-level backstop for the row lock: compile `session_ticket`'s query for MariaDB and assert it holds `FOR UPDATE`. The MariaDB test stays the real guard | 696f5aa |
| 12 | 7d | advisory | `kerbside/tests/unit/test_sources_proxmox.py` | One test that mints through `minting.mint_with_deadline` with a Proxmox source row, so the driver's construction from the database is exercised beside the generic fakes | 6519c3b |

### Resolution

The follow-up pull request, `proxmox-source-phase-07-fixes`, fixed
eleven of the twelve findings, one commit each, except findings 1 and
2, which share files and so share one commit. It declined finding 10. Each fix has a test, except the
documentation fixes 6 to 9 and the markers in finding 5. Each new test
fails on a copy of the tree with its fix removed.

| # | Commit |
|---|--------|
| 1, 2 | Hold the Proxmox driver to https and its own proxy. |
| 3 | Unregister a minting worker that fails to start. |
| 4 | Read qemu as the backend in the TLS claims check. |
| 5 | Mark the Proxmox work's deliberate prints. |
| 6 | Name Proxmox's ports in the firewall summary. |
| 7 | Say how the minting sequence differs on Proxmox. |
| 8 | Trim the Proxmox source row in ARCHITECTURE.md. |
| 9 | Say the backend ticket columns reach backups. |
| 10 | Declined; see below. |
| 11 | Check the session ticket row is locked in a unit test. |
| 12 | Mint a Proxmox ticket through mint_with_deadline. |

Three things the fixes found:

- **Finding 2's case handling.** A mutant dropping a case-folding call
  survived, because `urlsplit()` already lowercases hostnames, so the
  call was removed.
- **Finding 4's proof.** It was shown on the real page. With every
  word in the checker's condition vocabulary stripped from the
  page's bullet, the checker now fails, and before the fix it
  passed.
- **Finding 10 is declined.** The fix was made and then reverted on
  the same branch. Emitting `::add-mask::` prints the value to stdout
  for the runner to read, and CodeQL's clear-text logging rule flagged
  that print as a high-severity alert, following the data from the
  `.vv` password. The flag is accurate about the data flow, whatever
  the intent. Masking also covered less than first claimed: it cannot
  hide output printed before a value is learned. The held values stay
  protected by the redaction every log tail passes through, and by
  Kerbside never logging a ticket, which 7f1 checked. A defence in
  depth that leaves a standing high-severity alert, or needs one
  dismissed, costs more than it adds.

7d's surviving mutant, the row lock, is now caught by finding 11's
unit test.

### Filed: future work no issue tracked

- #656: the Proxmox mint cutoff counts from the driver's start, not the
  caller's deadline (phase 4).
- A comment on #138: the tunnel fields and the direct path's `as u16`
  port casts (phase 3b).
- #657: share the e2e lane helpers between `ovirt-e2e` and
  `proxmox-e2e` (phase 5). 7c measured the overlap at about 150 to 200
  lines.
- #658: re-read the node subject when a VM live-migrates, and test it
  on two nodes (phases 4 and 5).
- ryll #666: seven tunnel and lane follow-ups (phases 1b and 2). The
  fork guards were already ryll #470.
- actions #216: create the documented least-privilege account, plus
  four substrate follow-ups (phases 1b and 5).

Every other *Future work* entry is done, or is documented as a
limitation where an operator reads it. Two are on the use-case page:
ACME on the API, and a load balancer needing port 3128. One is in
`proxy-architecture.md`: serialising mints across daemons.

### Filed, because they predate the plan

- #659: the checker's `CRYPTO` pattern does not match a bare "is TLS"
  (7e).
- #660: the scrape loop logs a source exception's text (7f2).
- #661: the `.vv` `title=` line is not escaped (7f2).
- ryll #665: `ConnectionConfig`'s derived `Debug` would print the
  password and host (7f1).

### Declined, and why

- **SSH host-key checking is off in the lane** (7f2, low). The oVirt
  and Shaken Fist lanes do the same against their ephemeral nodes. The
  key is public-key only, with no agent forwarding, so a
  man-in-the-middle could only feed the lane false results. Pinning
  needs the action to publish the node's host key. That is a change
  for every lane, not one this plan made.
- **The node's address reaches the uploaded `kerbside.env` and the
  step log** (7f2, informational). The node is ephemeral, its address
  is RFC 1918, and the deploy action prints the same values.
- **`tools/proxmox-e2e/drive-console.py` (1,484 lines) and
  `test_proxmox_e2e_tools.py` (1,581 lines)** (7c, advisory). Both are
  past the size block's marks. The test file's 25 test classes are
  grouped by the script they test, so a split by script is a clean
  seam. But the scripts it would follow are the ones #657 restructures
  into shared helpers. Both splits are deferred to that work, so the
  test files are cut along the seam the code ends up with, not a cut by
  line count made now.
- **The `dabe2f66e369` downgrade deletes rows whose names are too long
  for the old width** (7b). This is deliberate and warned about: a
  narrower column cannot hold them.
- **The driver's request timeouts are constants, not configuration**
  (7b). They match `ovirt.py`'s, and each one's comment gives its
  reason.
- **The 7f1 informational items** are already documented in
  `proxy-architecture.md` and the use-case page:
  - cross-daemon mints;
  - gRPC pool starvation;
  - the plaintext CONNECT;
  - a connect failure's message naming the proxy's host and port, which
    is not a credential;
  - the host-subject refusal living in ryll, which the direct-qemu
    lane's no-subject mode guards.
- **`tools/direct-qemu/run-tunnel-checks.sh` uses a predictable `/tmp`
  work directory** (7f1). This follows the harness's existing pattern
  on ephemeral runners.
- **Three `TODO(mikal)` lines in `api.py`, `db.py` and `main.py`**
  (7c). The mechanical script lists them only because the plan changed
  those files. No merge added them.
- **Double quotes in `tools/proxmox-e2e/account_commands.py`'s module
  docstring and in the generated `kerbside_pb2.py`** (7b). The first is
  a docstring, where the rule asks for double quotes. The second is
  generated.
- **`AGENTS.md`'s add-a-source procedure** (7e, mostly predates). The
  plan added one step, which names the hook. The procedure's home is a
  question for the whole file, not for this plan.

## Back brief

Before executing any step of this plan, back brief the operator
on your understanding of the plan and how the work you intend to
do aligns with it. Three points need Mikal's answer before 7a runs:

- **Decision 3:** 2d split into two opus agents at high effort, and 2c
  on opus, both above the runbook's settings.
- **Decision 5:** fixes in a follow-up pull request, not this one.
- **Whether this session may push and open the pull request** once the
  definition of done passes, and file the 7g issues once their wording
  is shown.
