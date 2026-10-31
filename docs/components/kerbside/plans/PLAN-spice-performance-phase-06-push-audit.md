# SPICE performance phase 6: push audit of phases 1-5

Master plan: [PLAN-spice-performance.md](/components/kerbside/plans/PLAN-spice-performance/).

## Planning effort

This plan was written in a high-effort session. The master plan gives
the phase no effort level, only the push-audit shared block. Review
effort: medium. The judgment is in the range, in which repository's
rules apply to which landing, and in how the review is split. The
steps themselves follow the runbooks.

## Scope

In scope:

- Running kerbside's `PUSH-AUDIT.md`, waves 1 and 2, over everything
  phases 1-5 landed in this repository: seven merges carrying content
  outside `docs/plans/`, listed under *Audit range*.
- Running ryll's `PUSH-AUDIT.md` over ryll #406, which phase 3 landed
  and nothing has audited (survey finding 3).
- Applying the shared block's floor checklist, plus questions of its
  own, to the two kerbside-patches landings. That repository has no
  `PUSH-AUDIT.md` (survey finding 2).
- Reviewing the carried qemu v2 series, the only code in the plan that
  no upstream reviewer will read (decision 4).
- The floor checklist's items 2 to 5 for kerbside too, as the Proxmox
  audit did.
- Triaging every finding as blocking or advisory, with the reason, in
  this plan's *Outcome*.
- Filing problems that predate the plan as issues.

Out of scope:

- Fixing findings. They land in follow-up pull requests after this one
  merges, one per repository that has any (decision 7).
- andris #56 (phase 4), whose push audit is recorded in its pull
  request body (survey finding 4).
- Phase 2's dri-devel submission of the kernel fix. It is not yet
  sent, and its audit is the upstream review (decision 6).
- The spice-server and Linux series' code. Their disposition is
  `submit`, so the upstream review is their audit (decision 4).
- The open audit-tooling defects #488, #491 and #638. This plan works
  around them (decision 1) and does not fix them.

## Audit range

Every merge below is a merge commit on its repository's default
branch's first-parent chain. Its diff against its first parent is the
whole of what that pull request landed. "Non-plan content" means
additions outside `docs/plans/`.

### kerbside

| Merge | PR | Date | Phase | Non-plan content |
|-------|----|------|-------|------------------|
| 5204616 | #478 | 2026-09-25 | 1. Proxy backpressure, re-baseline | about 2,340 lines: socket options in `kerbside/config.py`, `proxy_supervisor.py`, `etc/kerbside.conf.example` and their tests; `rust/kerbside-proxy/src/backend.rs`, `listen.rs`, `main.rs`; the shaped-link rig in `tools/shaped-link/` (seven files, including `guest/keydraw.c`, `run-matrix.sh`, `summarise.py`); two `docs/performance/` pages |
| 2e19d73 | #489 | 2026-09-26 | 3. Planned | none: `docs/plans/` only |
| eab2c57 | #494 | 2026-09-27 | 3, steps 1a-1b. Client caps | about 990 lines: `rust/kerbside-proxy/src/caps.rs` (441, new), `session.rs` (+301), `backend.rs`, `rpc.rs`, a ryll pin bump; four protocol and architecture pages |
| f51b9d3 | #497 | 2026-09-27 | Phases 7-9 added | none |
| 9d1b64d | #505 | 2026-09-28 | 3. #494's review follow-ups | about 140 lines: `session.rs` (+119), `backend.rs`, `rpc.rs`; four docs pages and one line of `AGENTS.md` |
| 13e9e2e | #581 | 2026-10-19 | 3. Re-scoped by the RDP plan | none |
| bba7115 | #616 | 2026-10-28 | 3, steps 2 and 3b. ACK window, transcoder | about 8,110 lines: `ackwindow.rs` (554, new), `metrics.rs`, `relay.rs`, `backend.rs`, `main.rs`; `src/transcode/` (about 5,900 lines, new, behind the `transcode` feature); `Cargo.toml`, `Cargo.lock` (+531) and the `Makefile`; `summarise.py`; eight docs pages including `ARCHITECTURE.md` |
| 7919ead | #630 | 2026-10-28 | 3, steps 4-6. Measure, switch, verdict | about 4,070 lines: `transcode/switch.rs` (883, new), `transcode/mod.rs`, `tests.rs`, `backend.rs`, `main.rs`, `relay.rs`; two steps in `.github/workflows/rust.yml`; five new or grown shaped-link tools; three `docs/performance/` pages and two images |
| a90f17f | #637 | 2026-10-29 | 4. D-Bus display spike | about 1,500 lines: `tools/shaped-link/build-andris.sh` (new), `run-matrix.sh`, `summarise.py`, `keydraw.c`; four `docs/performance/` pages, two images and `andris-sessions.txt` |
| 46c3e98 | #654 | 2026-10-30 | 5. spice-server patches | about 320 lines, all docs: `docs/performance/spice-server-patches.md` (264, new) and corrections to five sibling pages |

The phase merges are the master plan's `Merged` cells. The plan-only
merges were found with `git log --merges --first-parent origin/develop`
on branch names, and with `gh pr list --state merged --search
'spice-performance in:body,title'`. They are listed because the shared
block counts a change to the plan's own text as the plan's work. #489,
#497 and #581 are not run through the wave scripts, which would pass
vacuously; 6e reads the master plan's final text.

### Other repositories

| Repository | Merge | PR | Phase | Content | Audit |
|------------|-------|----|-------|---------|-------|
| kerbside-patches | b53aa39c7e | #1748 | 2. Import | about 5,000 lines under `upstream/`: the qemu v1 and v2 series, the Linux series, their rigs (shell, C and Python) and results; `.gitignore`, `.pre-commit-config.yaml` | this phase, 6h and 6i (survey finding 2) |
| kerbside-patches | 2d7b626422 | #2024 | 5. spice-server series | about 2,400 lines under `upstream/spice/`: the series, two dropped drafts, `rig/build-spice.sh`, `rig/summarise-spice.py`, results; `upstream/README.md` | this phase, 6h (survey finding 2) |
| ryll | fdb5ead5b4 | #406 | 3, step 1a. Caller-chosen link caps | about 780 lines in `shakenfist-spice-protocol`: `link.rs` (+500), `client.rs` (+243), `reader.rs`; `docs/spice-protocol.md` | this phase, 6g (survey finding 3) |
| andris | 9a997a3 | #56 | 4. D-Bus display platform | the qemu platform | its pull request body (survey finding 4) |
| linux | not yet | | 2. Kernel fix | the one-line core fix | dri-devel review, once sent (decision 6) |

## What the survey found

1. **Phase 3's `Merged` cell recorded a SHA that does not resolve.**
   `9d1b64d6` is not a prefix of #505's merge commit, which is
   `9d1b64deae`. `git rev-parse 9d1b64d6` fails, so an audit reading
   the cell would have skipped #505's 140 lines of `session.rs`. The
   planning commit corrects the cell.

2. **kerbside-patches has no `PUSH-AUDIT.md`, and the audit the master
   plan says to cite does not exist.** The master plan's phase 2 and
   phase 6 sections both say this phase cites kerbside-patches' own
   audit. kerbside-patches' `docs/plans/upstream-series.md` does carry
   a push-audit phase (its phase 5), but its status table reads `Not
   started`. Its phase 1, the import, still reads `In progress` though
   it merged as #1748. #2024 landed under no kerbside-patches plan at
   all. The template's shared block, now at v4, says what a repository
   without a runbook does instead: the floor checklist, plus the
   questions its own blast radius raises, and not another repository's
   runbook. The master plan still carries v3 of the block, which
   predates the checklist. The planning commit syncs the block
   verbatim from `PLAN-TEMPLATE.md`, and corrects both sections. The
   stale status table in kerbside-patches is left for that repository's
   follow-up (6j).

3. **ryll #406 was never audited.** Phase 3 recorded it in its
   `Merged` cell. Neither its pull request body nor its comments carry
   an audit, and no plan in ryll's `docs/` mentions it. ryll's
   push-audit merges since then (#411, #630, #633, #657) each audit a
   different plan. Phase 3's re-survey decision 1 says andris and ryll
   audit the work that step 3a found already delivered. That covers
   other pull requests, not #406. kerbside's proxy consumes #406
   through `caps.rs` to parse a client's link message, which is
   untrusted input, so this one is worth the opus agent.

4. **andris #56 was audited, and the record is in its pull request
   body.** Style passed and nothing was blocking. The one medium
   finding was fixed in the pull request's second commit, and two items
   were declined with reasons. This phase cites it.

5. **The plan changed Rust in five merges, and `PUSH-AUDIT.md` has no
   Rust gate.** Wave 1 is tox only. `rust.yml` runs `cargo fmt
   --check`, clippy with `-D warnings`, `cargo test` and a release
   build on every push to `develop` that touches `rust/**`. Since
   #630 it also runs clippy and the tests with `--features transcode`.
   #616 added the transcoder before those two steps existed, so its own
   push run never built the feature.

6. **Most of the Rust diff is not shipped.** The transcoder (about
   7,000 lines over #616 and #630) sits behind the `transcode` feature.
   `Cargo.toml` says `tools/build-proxy-wheel.sh` never enables it,
   because it builds openh264 from source. The shipped surface is
   `caps.rs`, `ackwindow.rs` and the changes to `session.rs`,
   `backend.rs`, `relay.rs`, `metrics.rs`, `listen.rs` and `main.rs`:
   about 2,400 lines. That is the floor checklist's item 5 here: a
   build that enabled the feature by mistake would publish an
   unreviewed decoder in the dev wheels.

7. **The measurement tools have one test file, and nothing runs it.**
   `tools/shaped-link/` grew through four phases: `run-matrix.sh` to
   643 lines, `summarise.py` to 605. Its only test is
   `test_quality.py`, which neither `tox.ini` nor
   `.pre-commit-config.yaml` mentions. Every verdict in phases 1, 3, 4
   and 5 was computed by `summarise.py`. This is a candidate for 6d,
   not yet a finding: it is a rig, not shipped code.

8. **Several verdicts were overturned by later phases, and phase 5
   corrected five pages for it.** Phase 1's backpressure finding was
   null; phase 3 found the ACK window bounds the backlog but does not
   cap throughput; phase 5 withdrew the GLZ half of item 9, narrowed
   the crash claim, and showed the MJPEG codec was Ryll's choice, not
   a proxy fault. Drift between the ten `docs/performance/` pages
   is the failure 6e looks for first.

9. **Phase 2 is still `In progress`.** What remains is sending the
   kernel fix to dri-devel. It changes nothing in kerbside, and
   nothing in kerbside-patches unless review asks for a v2
   (decision 6).

The master plan's phase 2, phase 3 and phase 6 text and the
`docs/plans/index.md` row were corrected at source in the planning
commit, for findings 1 to 3. Later steps need not redo that.

## Decisions

1. **Audit merge by merge.** For each kerbside merge `M` carrying
   Python or shell, the range is `M^1..M` with `AUDIT_PATHS` unset.
   That is exactly one pull request's diff, and it sidesteps #491 and
   #638; the Proxmox and deployment-options audits did the same. Wave
   1 runs on #478, #616, #630 and #637. #494 and #505 carry only Rust
   and docs, and #654 only docs, so a tox run there tests a tree the
   diff did not touch. Their Rust is gated by decision 2 and their
   docs read by 6e. The judgment agents read all seven diffs together,
   because what the phases did to each other is the shared block's
   real point, and each brief names a cross-phase question.

2. **Rust is gated by CI's own runs.** 6a records `rust.yml`'s push
   run on each of the five merges that touched `rust/**`. Because #616
   predates the feature steps, 6a also records the newest `develop`
   run, which builds and tests `--features transcode` on today's tree.
   A missing or failed run falls back to the Makefile's `lint` and
   `test` targets in Docker, against a detached worktree of that
   merge.

3. **kerbside-patches gets the floor checklist, not kerbside's
   runbook.** The v4 shared block says so in terms (survey finding 2).
   Its blast radius adds questions of its own, which 6h asks:
   - Does anything under `upstream/` become visible to the OpenStack
     tooling, which discovers projects as `./<project>/config.yaml`?
   - Do the rigs assume a scratchpad or home path?
   - Is the rig code imported from elsewhere licensed to be here?
   - Does every patch carry the attribution its upstream requires
     (`Assisted-by:`, and no `Signed-off-by` until Mikal adds his)?
   - Can `build-spice.sh` remove anything it did not create?

   This audit is also what kerbside-patches' own upstream-series push
   audit can cite for #1748 and #2024 when it runs.

4. **The carried qemu v2 series is reviewed here; the submitted series
   are not.** The spice-server and Linux series go to their upstream
   maintainers, and that review is their audit. qemu v2 is
   `disposition: carry`. No qemu maintainer will read it, and the
   master plan says its audit is kerbside-patches' own. It is not in
   any image today. Whether it reaches the Kolla nova-libvirt image is
   an open question in upstream-series' *Unresolved*. If it does, it
   runs in every hypervisor's display path, parsing guest-controlled
   damage. So 6i reads its four patches for memory safety and
   correctness now, while that decision is still open. A reviewer
   could argue either side: that code nobody ships needs no review, or
   that the spice series deserves the same read before it is sent.
   The spice series had phase 5's own review and measurements, and
   goes to a maintainer who takes AI-assisted patches. The qemu series
   will only ever be read by us.

5. **ryll #406 is audited here, under ryll's runbook.** The shared
   block expects a landing in another repository to be audited there
   and cited. #406 never was (survey finding 3), so 6g runs ryll's
   `PUSH-AUDIT.md` over `fdb5ead5b4^1..fdb5ead5b4`, waves 1 and 2, in
   a detached ryll worktree. Its findings go to ryll as issues, or as
   a ryll follow-up pull request if they are fixes worth making.

6. **The audit runs now, with phase 2 unfinished.** Phase 2's
   remaining work is an email. Its patch text was imported in #1748,
   which 6h audits. Waiting for dri-devel would hold the QUIC phases
   for weeks for no benefit to the audit. If review asks for a v2 of
   the kernel patch, it lands in kerbside-patches as its own pull
   request, and its upstream review is the audit. The plan stays `In
   progress` until phase 2 is `Complete`, whatever this phase finds.
   This is the decision a reviewer is most likely to argue with.

7. **Findings are fixed in follow-up pull requests, one per repository
   that has any.** This phase's pull request is the record: the
   ranges, every wave's result, and every finding with its triage.
   Once it merges, the kerbside follow-up records this phase's merge
   commit and marks phase 6 `Complete`. The kerbside-patches and ryll
   follow-ups, if any, are cited there. If nothing needs fixing
   anywhere, this pull request marks phase 6 `Complete` itself, with
   no `Merged` cell.

8. **One 2d agent for kerbside, reading the transcoder at lower
   depth.** The shipped surface is about 2,400 lines (survey finding
   6), which one opus agent can hold. The transcoder is read for one
   question only: can it run in a shipped build? That means the
   feature gate, the wheel build script, and what `--transcode-display`
   does without the feature. It is not read line by line for memory
   growth or decoder bugs. Those would matter only if it ever ships,
   and the phase 3 verdict was no-go.

9. **Keep the record public-safe.** The *Outcome* holds verdicts,
   paths, commands and counts. It quotes no hostname, address, ticket
   or token, even from a rig script or a captured log. The rigs and
   the measurement pages were written on a private host, so the
   floor checklist's item 3 is live here. The definition of done runs
   a probe over the text this phase adds.

## Step plan

6a runs first, because kerbside's wave 2 is only worth spending on if
wave 1 passes. 6g, 6h and 6i work in other repositories, in their own
detached worktrees under their scratch directories, so they start
alongside 6a. 6b to 6f only read this worktree, so they spawn in
parallel once 6a is green. Each writes only to its own scratch
directory. 6j depends on all of them.

Every kerbside agent works from the *Audit range* table. "The seven
diffs" means `git diff M^1 M -- . ':!docs/plans'` for each kerbside
merge with non-plan content, oldest first. An agent reads `docs/plans/`
only for what its brief asks. Mutation runs happen on copies, never on
a worktree, one per run under `timeout` (300 s for Python, 900 s for a
Rust build in Docker), and a timeout counts as killed. No agent prints
a hostname, address, ticket or token from a rig, fixture or log; each
reports names and counts only.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | low | sonnet | none | Wave 1, kerbside. For each of 5204616, bba7115, 7919ead and a90f17f, oldest first, run `AUDIT_RANGE="$M^1..$M" tools/audit/wave1.sh` with `AUDIT_PATHS` unset, and report each exit code and its advisory output (codes are in `PUSH-AUDIT.md` under Wave 1). Do not use `plan-range.sh` (decision 1), and never change a range to make a run pass. If any run exits 6, stop and report. The flake8 gate ignores `AUDIT_RANGE` (#488), so also run flake8 at 120 columns from the tox environment over the Python files each merge added or changed that still exist, and report per merge. Then Rust (decision 2): with `$HOME/bin/ci-status shakenfist/kerbside runs --branch develop --limit 200`, find `rust.yml`'s push run on 5204616, eab2c57, 9d1b64d, bba7115 and 7919ead, and the newest `develop` run of `rust.yml`; report each conclusion and run ID, and confirm the newest run executed both `--features transcode` steps. If any is missing or failed, run `make lint` and `make test` in `rust/kerbside-proxy/` (Docker) against a `git worktree add --detach` of that merge in your scratch directory, and remove the worktree afterwards. Report in under 350 words. |
| 6b | low | sonnet | none | Style conformance, the judgment portion. Execute `PUSH-AUDIT.md`'s "Style conformance — judgment portion" brief over the seven diffs, in place of `git diff develop...HEAD`. The conventions are in `AGENTS.md`. The material is the Python in #478 (`config.py`, `proxy_supervisor.py`), the Rust in #478, #494, #505, #616 and #630, and the rig in `tools/shaped-link/` (shell, Python, C). Look hardest at config (the socket options in #478: pydantic, defaults, inline documentation, and `etc/kerbside.conf.example`), logging in the Rust (levels, and whether a client-supplied value reaches a log line), and the field-rename bullet (#494 and #505 changed what `rpc.rs` carries; did any field change units or meaning without a rename?). Apply the Python rules: single quotes except docstrings, no triple single quotes, 120 columns. Say "no material" for any bullet with none. Report in under 300 words. |
| 6c | medium | sonnet | none | Wave 2 mechanical, plus 2a code quality. Run `AUDIT_RANGE="$M^1..$M" tools/audit/wave2-mechanical.sh` once for each of the seven merges, and report its output; it never exits non-zero on findings. Then execute the 2a brief in `PUSH-AUDIT.md` over the seven diffs, with that output as input. Its shared blocks bind: `python-version-discipline`, `comment-proportion`, `source-file-size` and `plan-references-in-code`. The size block has material: `transcode/geometry.rs` (1,193), `transcode/mod.rs` (1,120), `transcode/tests.rs` (1,242), `session.rs` (982), `switch.rs` (883), `backend.rs` (872), `relay.rs` (821), `run-matrix.sh` (643), `summarise.py` (605). For the Rust, say which file the plan pushed past a mark, against which merge. Cross-phase questions: `run-matrix.sh` and `summarise.py` grew in four phases each; has either kept modes or code paths no later phase uses? Phase 3 copied andris's update pipeline into `src/transcode/updates/` at a named andris commit; is that commit recorded in the code, so the copy's drift is visible? Floor checklist item 2: did any diff add something that runs unattended (the two `rust.yml` steps run on push and fail the run; say whether anything else does)? Item 4: #494 and #505 changed `rpc.rs` and the ryll pin, and #616 bumped `Cargo.lock`; grep this repository, `../ryll` and `../kerbside-patches` for any consumer of a removed or renamed item, and state the greps. Report blocking or advisory per finding, with file and line, in under 450 words. |
| 6d | medium | sonnet | none | 2b test review over the seven diffs. The `functional-test-coverage` shared block binds. Answer directly whether each guard below fails if what it guards regresses, by mutating a copy of the tree in your scratch directory. Python: run only the relevant test file under `timeout 300`. Rust: `make test` in the copy's `rust/kerbside-proxy/` under `timeout 900`, at most eight Rust mutants in all, and a timeout counts as killed. The guards: the socket-option config validation and its pass-through to the proxy's command line (`config.py`, `proxy_supervisor.py`, `test_config.py`, `test_proxy_supervisor.py`); `caps.rs` rewriting a client's capabilities, including a malformed or oversized link message and the codec-preference passthrough; #505's follow-ups in `session.rs`; `ackwindow.rs` holding the backlog at 2W+1, and what a client ACK of zero, or far beyond the window, does; the new metrics' label set staying bounded (`metrics.rs`); and, under the `transcode` feature, `switch.rs` refusing to switch while GLZ or streams are live. Survey finding 7: is `tools/shaped-link/test_quality.py` run by anything, does it pass today, and which `summarise.py` paths that produced a published verdict have no test at all? Is that a finding against the plan, or a rig's normal standard? Keep the mutations in a script in your scratch directory and give its path. Report in under 400 words. |
| 6e | medium | opus | none | 2c documentation review over the seven diffs and the plan-only merges. Its four shared blocks bind (`readme-discipline`, `llm-doc-discipline`, `diagram-discipline`, `plan-phase-references`). Hold the pages together: the ten pages under `docs/performance/`, including `dbus-helper/summary.md`, `docs/proxy-architecture.md`, `docs/configuration.md`, `docs/testing.md`, `docs/development.md`, `docs/direct-qemu-harness.md`, `docs/spice/protocol-overview.md`, `spice-link-protocol.md` and `capabilities.md`, `ARCHITECTURE.md` and `AGENTS.md`. Survey finding 8 is the first question: does any page still state something a later phase overturned? In particular the backpressure finding, whether the ACK window caps throughput, GLZ invalidations as a server defect, which clients trigger the connect crash, and why streaming runs sent MJPEG. Check each against the newest page that owns it, and say which is right. Then the cross-repository check: do `docs/performance/spice-server-patches.md` and kerbside-patches' `upstream/spice/README.md` and `upstream/README.md` (read at `origin/develop` with `git -C ../kerbside-patches show`) agree on every number and verdict? Does `docs/configuration.md` describe #478's options as `config.py` defines them? Check every *Future work* entry in the five phase plans and the master plan for an issue, or somewhere live that still tracks it, reading GitHub only with `gh issue view`, `gh issue list` and `gh pr view`. Note that the sysroot bug in the master plan has no issue yet; Mikal is to file it. Grep `README.md`, `AGENTS.md`, `ARCHITECTURE.md` and `docs/` outside `docs/plans/` for "phase <number>" and for named people. Report blocking or advisory per finding, in under 550 words. |
| 6f | high | opus | none | 2d security review of kerbside's shipped surface: the diffs of #478, #494, #505, #616 and #630, outside `src/transcode/` (decision 8). Its `path-traversal-review` shared block binds. The material is: `caps.rs` and its callers in `session.rs` and `backend.rs`, which parse and rewrite a client's link message before the backend sees it (bounds on every length and count, what reaches the backend unchanged, and whether a client can make the proxy advertise a capability the operator did not allow); `ackwindow.rs` and `relay.rs`, which count client ACKs and server messages (integer overflow, unbounded state per connection, and a client that never ACKs); `metrics.rs` (label cardinality a client can drive); the socket options in `listen.rs`, `backend.rs`, `main.rs` and `config.py` (what an out-of-range value does, and whether one reaches `setsockopt` unchecked); and #505's changes to `rpc.rs`. For the transcoder, answer only decision 8's question: can it run in a published wheel? Check `Cargo.toml`'s features, `tools/build-proxy-wheel.sh`, `rust.yml`, and what `--transcode-display` does in a build without the feature. That is the floor checklist's item 5. Item 3: did any of the seven diffs, the two PNGs and one JPEG under `docs/performance/`, or `docs/performance/dbus-helper/andris-sessions.txt` put a private hostname, an address other than loopback or documentation ranges, a username or a token into this public repository? Check the images by viewing them. Also check the rig in `tools/shaped-link/` for commands run as root, `tc` or namespace setup left behind on failure, and temporary paths an attacker on the host could pre-create. Report severity, file, line, class and fix per finding, in under 550 words. |
| 6g | high | opus | none | Audit ryll #406 under ryll's own `PUSH-AUDIT.md` (decision 5). First confirm survey finding 3: search ryll's `docs/plans/` and the bodies of ryll's push-audit pull requests since 2026-09-26 (#411, #630, #633, #657, with `gh pr view`) for any audit whose range includes `fdb5ead5b4`. If one does, report it and stop. Otherwise create `git -C ../ryll worktree add --detach <scratch>/ryll-406 fdb5ead5b4`, and in it run wave 1 with `AUDIT_BASE=fdb5ead5b4^1 AUDIT_HEAD=fdb5ead5b4 tools/audit/wave1.sh`, as ryll's runbook describes, in Docker. Then execute ryll's judgment briefs, style, 2a, 2b, 2c and 2d, over that one diff yourself. Weight 2d: `link.rs` and `client.rs` parse link messages, and kerbside's proxy uses them to read an untrusted client's link message (`rust/kerbside-proxy/src/caps.rs`). Check every length, count and offset taken from the wire, and what a truncated or oversized message does. Also check whether ryll's later commits, up to the rev kerbside pins in `rust/kerbside-proxy/Cargo.toml`, changed the same functions, and if so audit the pinned version's code instead, saying so. Remove the worktree afterwards. Report severity, file, line and fix per finding, and say for each whether it is still present at ryll `origin/develop`, in under 500 words. |
| 6h | medium | sonnet | none | The floor checklist for kerbside-patches #1748 and #2024 (decision 3). Work in `git -C ../kerbside-patches worktree add --detach <scratch>/kp origin/develop`, and remove it afterwards. Run `pre-commit run --files` over every file the two merges added that still exists, and report per hook. Then give a verdict, or a sentence saying why it does not apply, on each floor item in `PLAN-TEMPLATE.md`'s `plan-push-audit-phase` block: 1, both landings are merge commits read against their first parents; 2, does anything added run unattended?; 3, scan every added file, including `results/`, patch headers and cover letters, for a secret, a token, a private hostname, an address other than loopback or documentation ranges, or a home or scratchpad path; 4, did either merge remove or rename anything a consumer still reads? (`.gitignore` and `.pre-commit-config.yaml` changed; grep for the old paths); 5, does `upstream/` publish anything? Then decision 3's own questions: discovery (does `find . -maxdepth 2 -name config.yaml`, or a `*/config.yaml` glob, now find an `upstream/` project? Name the scripts you checked); scratchpad or home paths in the rigs; the licence of the imported rig code (`upstream/qemu/rig/`, `upstream/linux/rig/`), and whether each file says where it came from; patch attribution (each series' trailers against its upstream's rule, as `upstream/README.md` and each project's README state it); and whether `upstream/spice/rig/build-spice.sh` can remove anything it did not create. Note, for the follow-up, that `docs/plans/upstream-series.md`'s status table is stale (survey finding 2). Report blocking or advisory per finding, in under 450 words. |
| 6i | high | opus | none | Review the carried qemu v2 series (decision 4): the four patches in kerbside-patches' `upstream/qemu/series/v2/`, read at `origin/develop` with `git -C ../kerbside-patches show`, against qemu at the `base_sha` in `upstream/qemu/config.yaml`. Clone qemu shallowly at that SHA into an empty directory under your scratch directory if you need the surrounding code, and do not build it. The question is what the series does with guest-controlled input: damage rectangles from the display device, surface dimensions, and the column-gap split. Check every rectangle's arithmetic for overflow and clipping against the surface, the bounded damage list's bound and what happens past it, the lifetime of anything queued to spice-server across a surface resize or display switch, and what `max-refresh-rate` pacing does at zero or an extreme value. Check that the 12-case unit test in `ui/spice-damage.c`'s patch covers the edges the code handles. Report severity, patch, function and fix per finding, in under 500 words. Say plainly if a finding would matter only once the series is built into an image. |
| 6j | high | (management session) | n/a | Triage and record. Take 6a to 6i's reports. Reproduce every finding against the tree before accepting it. Classify each as blocking or advisory, and decide: fix in that repository's follow-up, decline (with the reason), or file (if it predates the plan, using `git log -S` or blame against the merges in *Audit range*). Show Mikal the wording of any issue before filing it. Give a verdict on each floor item for kerbside and for kerbside-patches. Write it all into this plan's *Outcome*, per decision 9. Commit subject: "Record the SPICE performance push audit." If nothing needs fixing anywhere, also mark phase 6 `Complete` in the master plan and `docs/plans/index.md`, with no `Merged` cell, and leave the plan `In progress` (decision 6). |

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| An agent audits the wrong content: a contiguous range, `develop...HEAD` (empty here), a stale `develop`, or the unresolvable `9d1b64d6`. | Decision 1, the corrected cell, and every brief names its merges. 6j checks each 6a and 6c run names its range, and that there are four and seven runs respectively. |
| The kerbside-patches audit borrows kerbside's runbook, and reports tox results for a repository with no tox gate over `upstream/`. | Decision 3. 6h's brief names the floor checklist and the repository's own `pre-commit`. |
| 6g audits #406 as it merged, while kerbside runs a later ryll rev with different code. | 6g's brief compares against the pinned rev and audits that where they differ. |
| An agent copies a hostname or address from a rig, a result table or a screenshot into its report, and from there into the plan. | Every brief forbids printing values, and decision 9's probe runs over the text this phase adds before the commit. |
| A mutation run damages a worktree, or runs forever. | Mutations run on copies, one per run under `timeout`. 6j checks `git status --short` is empty in every worktree before committing. |
| Parallel agents trip over each other's builds. | Only 6a runs tox in this worktree, and alone. 6d works in a copy. 6g, 6h and 6i work in detached worktrees of other repositories. Docker builds share only the cargo cache, which is safe to share. |
| A finding is real but predates the plan, and gets fixed here, widening the follow-up. | The push-audit rules: file it as an issue. 6j decides with `git log -S` or blame against the merges. |
| Phase 2's v2 kernel patch, if review asks for one, lands after this audit and is never audited. | Decision 6: it lands in kerbside-patches as its own pull request, and the upstream review is its audit. Phase 2's section says so. |

## Definition of done

- [x] 6a ran `wave1.sh` four times, once per merge carrying Python or
      shell. The execution notes give each exit code and the
      per-merge flake8 result.
- [x] 6a recorded `rust.yml`'s conclusion and run ID for 5204616,
      eab2c57, 9d1b64d, bba7115 and 7919ead, and for the newest
      `develop` run with its transcode steps, or the result of the
      Docker run that replaced a missing one.
- [x] 6c ran `wave2-mechanical.sh` seven times, likewise.
- [x] 6g either cited an existing ryll audit covering `fdb5ead5b4`, or
      ran ryll's wave 1 over it and recorded the exit code.
- [x] 6h recorded a verdict on each of the floor checklist's five items
      for kerbside-patches, and the `pre-commit` result per hook.
- [x] *Outcome* lists every finding from 6a to 6i, each with a
      verdict: fixed in a named repository's follow-up, declined (with
      the reason), or filed (with the issue). If there are none, it
      says so in one sentence.
- [x] Every finding *Outcome* accepts was reproduced against the tree
      by the management session.
- [x] *Outcome* gives a verdict on each floor item for kerbside (items
      2 to 5) and for kerbside-patches (items 1 to 5).
- [x] `git rev-parse` resolves every SHA in the master plan's phase 1
      to 5 `Merged` cells, in the repository each names. Checked with:
      `grep -o '[0-9a-f]\{7,\}' <(grep '^| [1-5]\.' docs/plans/PLAN-spice-performance.md | cut -d'|' -f5)`,
      each SHA tried against kerbside, kerbside-patches, ryll and
      andris.
- [x] The private-data probe over the text this phase adds prints
      nothing else:
      `git diff origin/develop -- docs/plans/ | grep '^+' | grep -n -E '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}|\b([0-9]{1,3}\.){3}[0-9]{1,3}\b|stillhq|kasm|home\.|/tmp/claude|BEGIN [A-Z ]*PRIVATE KEY|password *[:=] *[^ <]'`.
      The probe's own line in this plan matches itself and is
      expected. Any other hit is removed, or shown to be a public
      example address.
- [x] `git status --short` is empty in this worktree, and every
      detached worktree the agents created has been removed
      (`git worktree list` in kerbside, kerbside-patches and ryll).
- [x] Phase 6's status in the master plan and `docs/plans/index.md`
      matches decision 7, and the plan's status stays `In progress`.
- [x] `pre-commit run --all-files` passes.

## Bugs fixed during this work

None yet.

## Future work

None yet.

## Execution notes

- 2026-10-31 AEDT, back brief. Mikal kept decision 6 (the audit runs
  with phase 2's kernel submission outstanding), decisions 4 and 5 (6i
  reviews the carried qemu series, 6g audits ryll #406), and decision 7
  (fixes in follow-up pull requests, one per repository). He gave this
  session permission to push and open the pull request once the
  definition of done passes, and to file the 6j issues after showing
  him their wording.
- 6a, 6g, 6h and 6i started together; 6b to 6f started once 6a passed.
  No agent wrote to this worktree, and every detached worktree an agent
  created in kerbside, kerbside-patches and ryll was removed.
- 6a ran `wave1.sh` on 5204616, bba7115, 7919ead and a90f17f: exit 0 on
  all four. Direct flake8 at 120 columns
  (`.tox/shared/bin/flake8`) over each merge's added or changed Python
  was clean.
- 6a could not find `rust.yml`'s push runs for 5204616, eab2c57,
  9d1b64d and bba7115: `ci-status` returns at most the newest 100 runs,
  which reach back only to 2026-10-28. For those four, `make lint` and
  `make test` ran in Docker against a detached worktree of each merge,
  and all passed. `rust.yml` passed on 7919ead (run 40754407510), and
  the newest `develop` run (40845903771) passed both `--features
  transcode` steps. The Docker runs built the default features only, so
  bba7115's transcoder is covered by today's run, not its own.
- 6c ran `wave2-mechanical.sh` on all seven merges: exit 0 on each.
- 6d ran 14 mutants on a copy of the tree, 6 Python and 8 Rust, and 12
  were killed. The survivors are kerbside follow-up items 9 and 10.
  6g ran 2 mutants on a copy of ryll at fdb5ead5b4. Both were killed,
  one only by the 900 s timeout (ryll#672, item 1).
- 6g confirmed survey finding 3: no ryll audit range contains
  fdb5ead5b4. kerbside pins ryll b33dea44, where two of #406's
  functions have since changed, so 6g audited the pinned code.
- Departures from the plan:
  - 6g's findings went to ryll as issues (#671, #672) rather than a
    ryll follow-up pull request. Decision 5 allowed either. All are
    advisory, and one predates #406.
  - 6i's high finding is filed (kerbside-patches#2032), not fixed in the
    follow-up. A fix means a v3 series, a qemu build and a re-run of the
    rig, and the series is in no image. The kerbside-patches follow-up
    adds a warning that v2 must not be built into one until it is fixed.
  - The master plan's *Future work* entries owned by kerbside are left
    there, not filed. While the plan is `In progress` that section is a
    live record. The entries owned by ryll, andris and kerbside-patches
    were filed in those repositories, and the master plan now cites the
    issues.
- 6j filed, with the wording Mikal was shown, on 2026-10-31 AEDT:
  kerbside#667, kerbside-patches#2032 and #2033, ryll#671, #672 and
  #673, and andris#62.

## Outcome

Audited on 2026-10-31. Wave 1 and wave 2's mechanical checks passed on
every kerbside merge, and on ryll #406. The judgment agents raised no
critical or medium finding in kerbside, kerbside-patches or ryll. The
one high finding is in the carried qemu v2 series, which is in no
image. The management session reproduced every accepted finding
against the tree. Fourteen kerbside items and six kerbside-patches
items go to follow-up pull requests. Seven issues are filed. Phase 6
stays `In progress` until the follow-ups land, and the plan stays `In
progress` until phase 2 is done.

### Wave results

| Check | Result |
|-------|--------|
| 6a, `wave1.sh` per merge carrying Python or shell | exit 0 on 5204616, bba7115, 7919ead and a90f17f; flake8 clean on each |
| 6a, Rust | `make lint` and `make test` passed in Docker on 5204616, eab2c57, 9d1b64d and bba7115; `rust.yml` passed on 7919ead (40754407510) and on today's `develop` with the transcode steps (40845903771) |
| 6c, `wave2-mechanical.sh` per merge | exit 0 on all seven; one justified `noqa: E402` (`tools/shaped-link/test_quality.py`); no TODO, assert, broad except or new dependency |
| 6d, mutation | 14 mutants, 12 killed; survivors are follow-up items 9 and 10 |
| 6g, ryll `wave1.sh` at fdb5ead5b4 | exit 0: pre-commit, rustfmt, clippy `-D warnings`, cargo test, wave 1b |
| 6h, kerbside-patches `pre-commit` over the two merges' files | exit 0; shellcheck, skillsaw and the line-reference check passed; nothing lints `upstream/**/*.py` or `*.c` (kerbside-patches item 6) |

### Floor checklist: kerbside

- **Item 1, landings:** every phase landing in this repository is a
  merge commit read against its first parent. The plan-only merges are
  listed in *Audit range*. andris #56 is cited; ryll #406 and the two
  kerbside-patches merges were audited here.
- **Item 2, unattended work:** met. The only addition is two steps in
  `rust.yml`, which run on push and fail the run. Nothing else runs
  unattended.
- **Item 3, secrets and addresses:** pass. The added lines hold only
  loopback addresses, an RFC 1918 address in a pre-existing test mock,
  and RFC 1918 addresses on the rig's namespace-only veth pair. Ticket
  values are dummy rig constants. All five images were viewed: test
  patterns, a qemu version in a title bar, and no metadata.
- **Item 4, removed or renamed:** no consumer is left. #494 and #505
  removed or renamed nothing public; the `rpc.rs` changes are to the
  test mock. Greps over this repository, ryll and kerbside-patches at
  `origin/develop` for `MockService`, `spawn_mock`, `caps::`,
  `ackwindow`, `--transcode`, `notsent` and `backend_rcvbuf` found no
  outside consumer.
- **Item 5, publishing:** fails closed. The transcoder cannot reach a
  published wheel: `default = []`, `tools/build-proxy-wheel.sh` passes
  no features, and both wheel workflows build only through it. Without
  the feature, `--transcode-display` is an unknown argument and the
  proxy refuses to start. Nothing asserts that a wheel was built
  without it (kerbside item 12).

### Floor checklist: kerbside-patches

- **Item 1:** pass. #1748 and #2024 are two-parent merges, read against
  their first parents.
- **Item 2:** does not apply. Nothing added runs unattended; the rigs
  are run by hand.
- **Item 3:** pass. No secret, private hostname, or address outside
  loopback and documentation ranges. The emails are public patch
  authors, mailing lists and attribution trailers.
- **Item 4:** pass. Both merges only add files, widen two hook
  patterns and add `.gitignore` entries.
- **Item 5:** pass. `upstream/` publishes nothing, and image tags hash
  only the directories `functional-tests.yml` names, which exclude
  `upstream/`.
- **Decision 3's questions:** the OpenStack tooling cannot discover an
  `upstream/` project (`_build/assemble-source.sh`, `imagebuild.sh`,
  `bump-source-shas.sh`, `test-patches-for-ci.sh`,
  `rebase-with-claude.sh` and `repush-openstack.sh` checked). No rig
  depends on a scratchpad or home path. The rig code is original apart
  from one function in `bench.c` (kerbside-patches item 2).
  `build-spice.sh` removes only an empty directory or its own output.

### To fix in the kerbside follow-up pull request

| # | Source | Severity | Where | Finding | Introduced by |
|---|--------|----------|-------|---------|---------------|
| 1 | 6e | blocking | `docs/performance/dbus-helper-measurements.md`, the stream-coverage bullet | Says the earlier pages report H.264 and that why spice-server chose MJPEG "was not established". `spice-server-patches.md` established it: Ryll's own preference. #654 corrected the sibling pages and missed this one | a90f17f |
| 2 | 6e | blocking | `docs/performance/streaming-rebaseline.md`, first caveat | Says spice-server chose H.264 because Kerbside advertised Ryll's capabilities (#477). The client was Ryll, which asked for H.264 first; the proxy relayed correctly | 5204616 |
| 3 | 6e | advisory | `docs/performance/transcoding-spike.md` (summary and the crash paragraph), `transcoding-measurements.md` | Describe the connect crash as 0.15.2's, triggered by any connect. Only a client that sends its codec preference early triggers it, and 0.16.0 and master crash too. The "every switch" claims hold, because the transcoder's backend dial always sends a preference | 7919ead |
| 4 | 6e | advisory | `docs/performance/proxy-backpressure.md`, the side finding | Its bold lead still says the ACK window caps throughput, corrected only further down. `ack-window.md` is right | 5204616 |
| 5 | 6e | advisory | `docs/performance/spice-server-patches.md`, the rate-control item | Says "stream frames" where the measurement and the page's own results say display messages. The master plan's entry is corrected in this phase's record | 46c3e98 |
| 6 | 6f | low | `rust/kerbside-proxy/src/caps.rs`; `backend.rs`, the client channel caps | The client's channel caps reach the backend verbatim, so the reply mask limits nothing: a client can enable spice-server features Kerbside never offered. Say in `caps.rs` that the reply mask is not a security boundary, and test what the allowlist does with a server message that only an unoffered capability enables. If that message is relayed, mask the forwarded caps to the reply set plus the codec bits | eab2c57 |
| 7 | 6f | low | `tools/shaped-link/quality.sh`, `QUALITY_VENV` | Defaults to a fixed path under `/tmp`, and runs that venv's python if it exists, so another local user can plant one. Default under `${XDG_CACHE_HOME:-$HOME/.cache}`, or refuse a venv the user does not own | 7919ead |
| 8 | 6f | advisory | `kerbside/config.py`, the two socket options; the proxy's matching flags in `main.rs` | Bounded at 2^32-1, but the kernel takes a signed int, so values above 2^31-1 are silently reinterpreted. Cap both at 2^31-1, in config and in clap | 5204616 |
| 9 | 6d, 6f | advisory | `rust/kerbside-proxy/src/ackwindow.rs`, the ACK arm | `in_flight -= window` is unchecked; use `saturating_sub`. The documented over-ACK behaviour, and a window of 0, are untested: a mutant clamping in-flight at 0 survived | bba7115 |
| 10 | 6d | advisory | `kerbside/tests/unit/test_config.py`, `test_negative_values_are_rejected` | Covers only `PROXY_BACKEND_RCVBUF_BYTES`; a mutant dropping `ge=0` on `PROXY_CLIENT_NOTSENT_LOWAT_BYTES` survived. Test both | 5204616 |
| 11 | 6g | advisory | `rust/kerbside-proxy/src/backend.rs`, `BACKEND_CONNECT_TIMEOUT`'s comment | Says the crate's connect has no timeout of its own; at the pinned rev it wraps link and auth in one. Reword, keeping the per-attempt budget's reason | bba7115 (the ryll pin bump) |
| 12 | 6f | advisory | `tools/build-proxy-wheel.sh` | Nothing asserts a wheel is built without the `transcode` feature. Refuse non-empty default features or `[tool.maturin]` features | bba7115 |
| 13 | 6c | advisory | `relay.rs` and `transcode/mod.rs`, the control-plane termination log | The same literal, which CI matches on, is written twice. Share one constant | bba7115 |
| 14 | 6b | advisory | `kerbside/config.py`, `PROXY_CLIENT_NOTSENT_LOWAT_BYTES`'s description | The string is split mid-phrase (`'... in '`, `'bytes: ...'`). Rejoin it | 5204616 |

### To fix in the kerbside-patches follow-up pull request

| # | Source | Severity | Where | Finding | Introduced by |
|---|--------|----------|-------|---------|---------------|
| 1 | 6i | high, filed | `upstream/qemu/README.md`; `docs/plans/upstream-series.md` *Unresolved* | The v2 series' diff helpers can read out of bounds when the guest writes its framebuffer mid-diff (kerbside-patches#2032). Say in both places that v2 must not be built into an image until that issue is fixed | b53aa39c7e |
| 2 | 6h | advisory | `upstream/qemu/rig/bench/bench.c`; `upstream/README.md` | `bench.c` reproduces qemu's `qemu_spice_create_update()`, which is GPL, with no notice, and `upstream/README.md` never states the licence of what `upstream/` carries. Add both | b53aa39c7e |
| 3 | 6h | advisory | `upstream/linux/series/v1/0001-*.patch` | Its trailer is `Assisted-by: LLM`. Use the form the kernel's `coding-assistants.rst` documents before the patch is sent; check the current text, which this audit did not | b53aa39c7e |
| 4 | 6e | advisory | `upstream/spice/series/v1/0000-cover-letter.patch` | Says "stream frames" where the results say display messages, as kerbside item 5 | 2d7b626422 |
| 5 | 6h | advisory | `docs/plans/upstream-series.md`, Status | Phase 1 reads `In progress` though #1748 merged, and the table has no `Merged` column. Record b53aa39c7e, and cite this audit for its phase 5 | b53aa39c7e |
| 6 | 6h | advisory | `.pre-commit-config.yaml` | Nothing lints `upstream/**/*.py`. Widen the flake8 hook to it | b53aa39c7e |

### Filed

- kerbside#667: `docs/spice/spice-link-protocol.md` contradicts itself
  on whether a token is single-use; the code makes it reusable until
  expiry. Predates the plan.
- kerbside-patches#2032: the qemu v2 diff helpers' unbounded loops
  (high), a zero-width box, a timer spin above 1000 Hz and deep
  recursion (6i).
- kerbside-patches#2033: the sysroot `build.sh` cannot extract what it
  downloads (found in phase 5).
- ryll#671: link errors other than `MissingRequiredCaps` are untyped
  strings, which kerbside string-matches. Predates #406.
- ryll#672: #406's follow-ups: an oversized-reply test that hangs
  instead of failing, a check kerbside has to re-implement, two files
  past the size mark, and ryll's wave 2 test counting.
- ryll#673: the ryll items in the master plan's *Future work*, plus
  ryll's `streaming-video` recommendation, which the measurements
  contradict (6e).
- andris#62: the five andris items in the master plan's *Future work*.

### Declined, and why

- **The transcoder's size and comment proportion**: `transcode/mod.rs`
  is 1,120 lines, and `geometry.rs` and `tests.rs` are past 800 (6c,
  advisory). The transcoder is an experiment the phase 3 verdict
  declined, behind a feature no wheel enables; splitting it is work on
  code nobody is changing. The same holds for 6c's low-confidence note
  that the backend task is detached rather than aborted if the outer
  future is dropped.
- **`relay.rs`, `backend.rs` and `session.rs` past 800 lines** (6c,
  advisory). Each crossed the mark by a little, in a merge that added
  coherent code. A split is its own change, along seams the next change
  to those files will show.
- **Type hints on `config.py`'s validator** (6c). No function in the
  file has them; adding them is a whole-file change.
- **`tools/shaped-link/test_quality.py` is run by nothing, and
  `summarise.py` has no tests** (6d, survey finding 7). The rig is not
  shipped, the measurement phases are over, and each verdict's raw
  results and summary tables are published, so a wrong exclusion is
  recoverable from the data. Wiring numpy into CI for a finished rig
  costs more than it protects.
- **`BUFFER_BDP` is undocumented outside the plans** (6c). It is a rig
  knob, described in `run-matrix.sh` where it is read.
- **The client's capability words are logged before authentication**
  (6b, 6f). ryll caps them at 16 words of integers, so the volume is
  bounded and nothing is injectable.
- **The rig's ticket is visible in the process list** (6f). It is a
  constant dummy value for a throwaway guest.
- **"scratchpad" in kerbside-patches prose, and `build-spice.sh`'s
  forgeable output marker** (6h). The first names no path. The second
  guards a directory the operator chose.
- **`switch.rs` has no refuse-to-switch code** (6d). That is the
  design: the client leg never carries GLZ, and the first transcoded
  frame clears streams. The mutant removing that guard was killed.

## Back brief

Before executing any step of this plan, back brief the operator on
your understanding of the plan and how the work you intend to do
aligns with it. Four points need Mikal's answer before 6a runs:

- **Decision 6:** the audit runs now, with phase 2's kernel submission
  still outstanding.
- **Decisions 4 and 5:** the widened scope. 6i reviews the carried
  qemu v2 series, and 6g audits ryll #406 under ryll's runbook. Both
  are opus at high effort, and the master plan named neither.
- **Decision 7:** fixes in follow-up pull requests, one per repository
  with findings.
- **Whether this session may push and open the pull request** once the
  definition of done passes, and file the 6j issues once their wording
  is shown.
