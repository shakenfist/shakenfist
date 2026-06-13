# PLAN-snapshot phase 09: host CLI consolidation and parity

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (`run_snapshot`,
`run_snapshot_list`, `run_snapshot_create` / `_delete` /
`_apply`, `run_snapshot_mutating_guest`, and
`snapshot_error_message` in `src/vmm/src/main.rs`; the
`SnapshotArgs` clap surface; the six verification harnesses
under `tools/`; the snapshot sections of `docs/quirks.md`), and
ground your answers in what the code actually does today. Do
not speculate about the codebase when you could read it
instead. Where a question touches on qemu behaviour, the
authoritative references are `qemu-img.c::img_snapshot` and
`img_open` in qemu 10.0.x (fetch from
`https://gitlab.com/qemu-project/qemu/-/raw/v10.0.0/...` if
needed) plus the locally installed `qemu-img` 10.0.8.

Phase plans for the parent master plan live alongside it in
`docs/plans/` and are named
`PLAN-snapshot-phase-NN-<descriptive>.md`. The master plan is
[PLAN-snapshot.md](/components/instar/plans/PLAN-snapshot/). This is phase 9 of
fourteen.

I prefer one commit per logical change, and at minimum one
commit per phase. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

The master plan's original phase 9 ("host CLI for mutating
modes") was hollowed out by phases 6–8, which each pulled
their mode's dispatch forward so the guest work could be
validated end-to-end. What remains is a **small consolidation
and parity phase**: three real CLI-surface divergences found
by planning probes, a snapshot-local boilerplate
consolidation, a parity harness codifying the CLI contract,
and documentation. This phase touches **no guest code and no
crate code** — host CLI, harnesses, and docs only.

### Empirically established behaviour (planning probes)

Probed against qemu-img 10.0.8 and the current instar build.
Three divergences (D1–D3) and two confirmed parities:

- **D1 — `-U` (`--force-share`) with a mutating mode.**
  qemu refuses: `qemu-img: Could not open 't.qcow2':
  force-share=on can only be used with read-only images`,
  exit 1, image untouched. **instar currently accepts `-U`
  with `-c`/`-d`/`-a` and performs the mutation** (probe:
  `instar snapshot -U -c s2` exited 0 and created the
  snapshot). `-U -l` succeeds under both tools. The
  master-plan assumption that `-U` could be a blanket no-op
  is wrong for the mutating modes.
- **D2 — bare `snapshot FILENAME` (no mode flag).**
  qemu **defaults to list**: prints the snapshot table, exit
  0 (`img_snapshot`'s action defaults to `SNAPSHOT_LIST`).
  instar's clap `ArgGroup { required = true }` rejects with a
  usage error, exit 2. Downstream scripts that rely on the
  bare-filename form would break.
- **D3 — mixed mode flags** (`-c x -d y`). qemu:
  `Cannot mix '-l', '-a', '-c', '-d'`, exit 1. instar: clap
  usage error, exit 2. The behaviours agree in substance
  (refusal, non-zero exit, no image access); the message and
  exit code differ. Working answer: keep clap's behaviour and
  document it — fighting clap for a one-code delta buys
  nothing (and instar's other subcommands already expose clap
  usage-error semantics).
- **Parity confirmed — `-q`.** For the snapshot subcommand
  `-q` is a de-facto no-op under both tools: success is
  silent anyway and error output is *not* suppressed (qemu
  printed `Could not delete snapshot 'missing'...` despite
  `-q`, exit 1; instar likewise prints its message, exit 1).
  The phase 6 quirk note ("`-q` has no visible effect on
  create") generalises to all four modes.
- **Parity confirmed — not-found exit codes** (1 under both
  tools, image untouched).

### Consolidation scope

`run_snapshot_list` (~326 lines) and
`run_snapshot_mutating_guest` (~268 lines) share most of
their KVM/VM/guest-memory/config-write/vCPU-loop boilerplate;
they differ in the message pump (renderer vs silent capture)
and the device-open mode (RO vs RW + capacity hint).
Codebase-wide, per-operation boilerplate duplication is the
convention (`Kvm::new` appears 14 times in `main.rs`), so
this phase does **not** attempt a cross-operation helper —
but two near-copies *within one subcommand* are worth
unifying while six harnesses stand guard: factor a
snapshot-local launch helper parameterised by open mode and
message handler, leaving both callers byte-identical in
behaviour. If the factoring fights the borrow checker or the
message-pump generics into unreadability, stop and keep the
duplication (open question 2) — this is an opportunistic
cleanup, not a mission.

### What phase 9 produces

1. **D1 fix:** `-U` with `-c`/`-d`/`-a` refused host-side
   before any open/guest launch, exit non-zero, message
   modelled on qemu's
   (`snapshot: force-share (-U) can only be used with
   read-only operations; -l is the only sharing-safe mode`
   or similar — keep qemu's sentence shape where cheap).
   `-U -l` unchanged (accepted; instar takes no image locks —
   documented no-op).
2. **D2 fix:** bare `instar snapshot FILE` lists snapshots,
   byte-identical to `-l` (drop `required = true` from the
   ArgGroup; an absent mode resolves to list in
   `run_snapshot`). `--output=json` composes with the bare
   form exactly as with `-l`.
3. **D3 documented**, not changed.
4. **Snapshot-local launch consolidation** per the scope
   note above (or a documented decision not to).
5. **CLI parity harness** `tools/snapshot-cli-parity.sh`:
   codifies D1/D2/D3 and the confirmed parities as
   assertions against both tools — `-U` × four modes
   (refusal leaves the image bit-identical), bare-filename
   list (byte-identical to `qemu-img snapshot` bare output),
   `-q` error passthrough and silent success, mixed-flags
   non-zero refusal, not-found exit codes, `--image-opts`
   rejection, `-f qcow2` accepted / `-f` non-qcow2 refused.
   Shellcheck-clean, same conventions as the six existing
   harnesses.
6. **Docs:** quirks — the `-U` matrix (list-only sharing,
   mutating refusal, instar-takes-no-locks note), `-q` no-op
   across all modes, the clap exit-code/message divergence
   for usage errors, the bare-filename default; master plan —
   phase 9 row → Landed, per-mode `-U`/`-q` claims in the
   Mission section corrected (the `-q` "suppress success
   line" description is vestigial — there is no success
   line).

### What phase 9 does not change

- Guest binaries, crates, wire ABI — untouched.
- List/create/delete/apply semantics — byte-identical
  (all six harnesses re-run as gates, plus the new parity
  harness).
- The `-q`/`-U` wire flags (`FLAG_QUIET`,
  `FLAG_FORCE_SHARE`) stay plumbed to the guest unchanged —
  the guest already ignores them; host-side enforcement is
  the fix.

## Mission and problem statement

After phase 9 lands:

```
instar snapshot image.qcow2            # lists, like qemu-img
instar snapshot -U -l image.qcow2      # accepted (no-op)
instar snapshot -U -c s1 image.qcow2   # refused, image untouched
```

and `tools/snapshot-cli-parity.sh` passes, pinning the CLI
contract (flag semantics, exit codes, refusal hygiene)
alongside the six byte-identity harnesses. The snapshot
subcommand's host surface is then complete and consistent for
phases 10–11 to baseline and test against.

## Open questions

### 1. Should `-U` mutating-mode refusal mimic qemu's message verbatim?

qemu's text mentions `force-share=on` and "read-only images"
— artefacts of its open-flags machinery. **Working answer:
keep the substance (refusal, exit 1, before touching the
image), adapt the wording to instar's voice, and note in
quirks that stderr text differs.** stderr text is not
baselined anywhere (phase 10 baselines stdout), so verbatim
matching buys nothing.

### 2. How hard to push the launch consolidation?

**Working answer: one snapshot-local helper, attempted once.**
Acceptance bar: both call sites shrink, the helper's
signature stays comprehensible (open-mode enum + a
message-handling closure or small trait), and all harnesses
pass unmodified. If the first honest attempt misses that bar,
keep the duplication and record why in the commit message —
the convention elsewhere in the file is duplication, so
failure costs nothing.

### 3. Does bare-filename list need its own harness fixture?

**Working answer: no separate fixture** — the parity harness
asserts `instar snapshot FILE` output is byte-identical to
`instar snapshot -l FILE` *and* to `qemu-img snapshot FILE`
(TZ-pinned) on an existing-style fixture. Three-way, one
scenario.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 9a | medium | sonnet | worktree | CLI fixes in `src/vmm/src/main.rs`. (i) D1: in `run_snapshot`, refuse `args.force_share` combined with any mutating mode before any file access — message per open question 1, exit non-zero via the existing `Err(...)` path; `-U` with list (explicit or bare) stays accepted. (ii) D2: drop `required = true` from the `SnapshotArgs` mode `ArgGroup`; in `run_snapshot`, an absent mode dispatches to `run_snapshot_list` (the `unreachable!` arm becomes the list default). Verify `--output=json` composes with the bare form. (iii) Audit the clap surface against `qemu-img snapshot --help` (synopsis: `[--object ...] [--image-opts] [-U] [-q] [-l | -a | -c | -d] file`) and note any remaining gaps in the back-brief rather than adding flags speculatively (`--object` is crypto-secret plumbing; instar refuses encrypted images guest-side, so omitting it stays correct). (iv) Unit tests where the file's conventions support them (message-table tests exist; clap-level behaviour is covered by the 9c harness). |
| 9b | medium | sonnet | worktree | Launch consolidation per open question 2. Factor the shared KVM/VM/guest-memory/GDT/page-table/config-write/vCPU-loop boilerplate of `run_snapshot_list` and `run_snapshot_mutating_guest` into one snapshot-local helper parameterised by (a) device-open mode (RO vs RW + capacity hint) and (b) guest-message handling (the list renderer pump vs the silent result capture). Both callers must remain byte-identical in behaviour — `./tools/snapshot-create-matrix.sh`, `-delete-`, `-apply-` matrices + all three refusal harnesses + a `snapshot -l` vs `qemu-img snapshot -l` diff are the gates, run after the refactor. If the factoring misses the open-question-2 bar after one honest attempt, revert it, keep the duplication, and say so in the back-brief and commit message — that outcome is acceptable. |
| 9c | medium | sonnet | worktree | Parity harness `tools/snapshot-cli-parity.sh`, modelled on the existing harnesses (set -euo pipefail, PASS/FAIL counters, work dir under mktemp, shellcheck-clean). Assertions, each against both tools where applicable: (1) `-U -c/-d/-a` refused, exit non-zero, image bit-identical before/after (sha256); qemu side asserted refusing too. (2) `-U -l` exit 0 under both. (3) Bare `snapshot FILE` — three-way byte-identity per open question 3 (TZ=UTC). (4) Bare form with `--output=json` equals `-l --output=json`. (5) `-q` on a failing delete still prints to stderr and exits 1 under both; `-q` on a successful create is silent under both. (6) Mixed `-c x -d y` exits non-zero under both with no image access (sha256 unchanged). (7) Not-found `-d` / `-a` exit 1 both tools. (8) `--image-opts` rejected by instar with its documented message. (9) `-f qcow2` accepted; `-f vmdk` refused with the format-driver message. Run the harness plus ALL six existing harnesses; record full results. |
| 9d | low | sonnet | worktree | Documentation. (i) `docs/quirks.md` snapshot section: the `-U` matrix (mutating refusal matching qemu's substance, wording differs; `-l` accepted as a no-op because instar takes no image locks); `-q` is a no-op for every snapshot mode under both tools (success silent, errors never suppressed) — generalising the phase 6 create-only note; usage errors (mixed modes, unknown flags) follow clap conventions (exit 2, clap message) where qemu exits 1 with its own text; bare `snapshot FILE` defaults to list, matching qemu. (ii) `docs/plans/PLAN-snapshot.md`: phase 9 row → Landed pointing here; in the Mission section, correct the `-q` description ("suppress success line" — there is no success line; it is accepted for CLI compat and has no effect) and the `-U` description (host-side no-op → list-only no-op, mutating modes refused); tick the phase 9 entry in the success-criteria list if one exists. (iii) `docs/usage.md` only if it documents snapshot flags (phase 7 established it does not). |
| 9e | low | sonnet | worktree | Full verification + commit. `make instar`, `make test-rust`, `make check-binary-sizes` (no guest changes — every binary byte-identical to phase 8; assert this), `make lint`, `pre-commit run --all-files`, the new parity harness, and all six existing harnesses. Single commit for 9a–9d following `~/.claude/CLAUDE.md` conventions (50-char first line ending in `.`, 75-char wrap, Prompt paragraph, Signed-off-by, Co-Authored-By with model + context window + effort). The message should cover: the three probe-found divergences and their resolutions (D1 fixed, D2 fixed, D3 documented), the consolidation outcome (done or declined, with the reason), the new parity harness, and that no guest or crate code changed. |

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. **This phase is dispatched as a single
sonnet agent** — a deliberate contrast point for the model
experiment: phases 7 and 8 were Fable on discovery-heavy
work; phase 9's discoveries are already pinned in this plan
(D1–D3 with probe transcripts), the implementation is
host-CLI + shell + docs, and the guest/crate layers are
frozen. If the agent stalls on the 9b refactor, the
documented fallback (keep the duplication) caps the risk.
Worktree isolation as always; verify the worktree is based on
the `snapshot` branch head first (phases 6–8 all started on
the wrong base; `git reset --hard snapshot` if so).

### Management session review checklist

- [ ] Read the changed files; confirm no guest/crate/ABI
      files were touched.
- [ ] `-U` refusal happens before any file open; the image
      hash assertion in the harness proves untouched.
- [ ] Bare-filename list is the real list path (not a
      reimplementation).
- [ ] If 9b consolidated: both callers' behaviour proven
      identical by harness re-runs; if declined: the reason
      is recorded.
- [ ] Every operation binary byte-identical to phase 8
      (no guest changes).
- [ ] Parity harness + six harnesses green; `make instar`,
      `make test-rust`, `make check-binary-sizes`,
      `make lint`, `pre-commit run --all-files` clean.
- [ ] Independent spot-check: `instar snapshot FILE` vs
      `qemu-img snapshot FILE` byte-diff on a multi-snapshot
      fixture; `instar snapshot -U -c` refusal with sha256
      unchanged.

## Administration and logistics

### Success criteria

Phase 9 is complete when:

* All steps land in one commit on the `snapshot` branch.
* D1 and D2 behave per the Mission section; D3 and the `-q`
  no-op are documented.
* `tools/snapshot-cli-parity.sh` exists and passes; the six
  prior harnesses are unregressed.
* No guest binary changed (byte-identical artefacts).
* All builds, tests, sizes, lint, pre-commit clean.
* The master plan's phase 9 row is Landed and its Mission
  flag descriptions are corrected.

### Future work created by this phase

- **`--object` / secret plumbing** — only relevant if
  encrypted-image support ever lands (tracked already under
  the master plan's encryption future-work item).
- **Cross-operation launch consolidation** — the 14-fold
  `Kvm::new` duplication across `run_*` functions is a
  refactor for a maintenance window outside this plan
  family, if ever.

### Bugs fixed during this work

The `-U`-with-mutating-modes acceptance (D1) is a real
parity bug shipped in phases 6–8 — the flag was plumbed but
never enforced; fixed here before phase 10 freezes baselines.
The bare-filename rejection (D2) likewise. Recorded with
probe transcripts in this plan.

### Documentation index maintenance

This is a phase plan, not a master plan: **not** added to
`docs/plans/order.yml`. The master plan's Execution table
already links this file; step 9d flips the row to Landed.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how the
work you intend to do aligns with that plan. Include the
clap-surface audit result (step 9a iii) and the consolidation
decision (step 9b) with rationale.
