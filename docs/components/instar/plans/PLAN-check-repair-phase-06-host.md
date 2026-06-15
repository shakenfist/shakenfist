# PLAN-check-repair phase 06: host CLI polish

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly and ground your
answers in what the code actually does today; do not speculate
when you could read. The code this phase touches is all host-side
in `src/vmm/src/main.rs`:

- `CheckArgs` (~line 2920): `repair: Option<RepairMode>` (the
  `--repair[=leaks|all]` flag — landed in phase 5), `chain: bool`,
  `quiet: bool`, `output` (human/json).
- `run_check` (~6632): the vCPU loop captures the `CheckResult`
  and sets `check_passed` (~6980: `FLAG_VALID && total_errors ==
  0 && chain_errors == 0`); the tail (~7078) maps `!check_passed`
  to `Err("image check failed…")` — i.e. exit **1** for any
  failure, which is **not** qemu's convention.
- `print_check_result` (~7086, human) and `print_check_result_json`
  — the qemu-style renderer. It does **not** yet show repair
  results.
- The `CheckResult` repair fields from phase 1: `repaired_leaks`,
  `repaired_refcounts`, `repaired_corruptions`,
  `FLAG_REPAIR_INCOMPLETE`; plus `corruptions`, `refcount_errors`,
  `chain_errors`, `leaks`, `total_errors`, `FLAG_VALID`,
  `FLAG_NOT_SUPPORTED`.

The parent master plan is
[PLAN-check-repair.md](/components/instar/plans/PLAN-check-repair/); this is phase 6 of
eleven. It is **host-only** — no guest binary change, so `check.bin`
stays byte-identical. The guest already emits everything this
phase renders and maps.

I prefer one commit per logical change, and at minimum one commit
per phase. The commit must build, pass tests, and have a clear
message.

## Situation

Phases 4–5 wired both repair tiers in the guest and pulled the
`--repair[=leaks|all]` flag forward. What remains is host-side
polish for qemu-img parity and usability:

1. **Exit codes.** `instar check` returns `0` on success and `1`
   on any failure. `qemu-img check` (and `-r`) use **0 = clean,
   2 = corruptions/errors, 3 = leaks only** (and report the
   *post-repair* state when `-r` is given). instar should match.
   The existing integration tests assert only `!= 0` for error
   cases and use qemu's rc as a `(0, 3)` oracle for output
   parity — none pin instar to exactly `1` — so moving to 0/2/3
   is safe (verified by reading `tests/test_check_formats.py`).
2. **Repair output.** After a repair the renderer should report
   what was fixed (qemu prints "Repaired N leaked clusters", etc.)
   and whether the repair was incomplete. The guest already sends
   `repaired_leaks` / `repaired_refcounts` / `FLAG_REPAIR_INCOMPLETE`;
   the host just renders them (human + JSON).
3. **`--repair` + `--chain`.** In `run_check` the `chain` branch
   opens the chain devices read-only and takes precedence over
   the repair branch, so `--repair --chain` would silently set
   `FLAG_REPAIR` against a read-only device — repair would
   fail-safe to `INCOMPLETE` but the combination is nonsensical.
   Reject it with a clear error.
4. **`--help` warning.** `--repair=all` is lossy and modifies the
   image in place; the help text (and a one-line stderr notice
   when it runs) should say so and recommend a backup.

### What this phase produces

- `run_check` maps the **post-repair** `CheckResult` to a process
  exit code: `0` clean, `3` leaks-only, `2`
  corruptions/refcount/chain errors (via `std::process::exit(2|3)`
  since `Err` ⇒ 1; `Ok(())` ⇒ 0). Genuine VM/I-O failures keep
  returning `Err` (exit 1). `FLAG_NOT_SUPPORTED` matches
  `qemu-img check`'s behaviour for unsupported formats (verify
  empirically — likely exit 0 with the "does not support checks"
  message).
- `print_check_result` / `print_check_result_json` gain a repair
  section: when `repaired_leaks + repaired_refcounts > 0` or
  `FLAG_REPAIR_INCOMPLETE` is set, render qemu-style lines
  ("Repaired N leaked clusters", "Corrected N refcounts",
  "Repair did not complete: …"); JSON gets matching keys.
- `run_check` rejects `--repair … --chain` early with a clear
  error message.
- `CheckArgs.repair`'s doc/`--help` text warns that `all` is
  destructive; `run_check` prints a one-line stderr notice when
  `--repair=all` runs.

### What this phase does NOT do

- No guest change (`check.bin` byte-identical).
- No new repair behaviour — only host rendering, exit-code
  mapping, and argument validation.
- Corrupt-fixture baselines and the round-trip integration tests
  are phases 7–8; this phase's verification is the gates, the
  existing `make test-integration` check suite, and smokes that
  assert the new exit codes (clean → 0, leaky → 3, corrupt → 2).

## Open questions

### 1. Do the 0/2/3 exit codes apply to all `check`, or only `--repair`?

**Resolved: all `check`.** The 0/2/3 convention is qemu's for
plain `check` too; applying it only under `--repair` would be
inconsistent. The existing tests assert `!= 0` for errors and
compare JSON (not exit code) for parity, so the broader change is
safe. Any test that breaks was asserting the old 0/1 behaviour
and should be updated to the qemu convention (none found in the
read-through).

### 2. Reject `--repair --chain` in clap, or at runtime?

**Resolved: runtime, in `run_check`.** A clap `conflicts_with`
is cleaner but `chain` and `repair` live on `CheckArgs` and the
chain path is also reachable via image-detected backing chains;
a runtime check at the top of `run_check` (before opening
devices) gives a precise message and is easy to verify. (A clap
`conflicts_with` is an acceptable alternative if it reads
cleanly.)

### 3. Warn on `all` via `--help` only, or also at runtime?

**Resolved: both.** The `--help`/doc text documents the
destructiveness; a single stderr line when `--repair=all` runs
("warning: --repair=all rewrites image metadata in place; back up
valuable images first") gives the in-the-moment nudge without
being a blocking prompt (qemu-img doesn't prompt either).

### 4. Exit code for an *incomplete* repair?

**Resolved: by the post-repair counts, not the flag.** qemu's
exit code reflects the re-check after repair, so a repair that
left leaks ⇒ 3, left corruptions ⇒ 2, fully fixed ⇒ 0.
`FLAG_REPAIR_INCOMPLETE` drives the *message* ("repair did not
complete"), not the exit code (the residual counts already
encode the state).

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | high | opus | none | Exit-code mapping + `--repair`/`--chain` reject in `src/vmm/src/main.rs`. (i) At the top of `run_check` (before opening devices), if `args.repair.is_some() && args.chain` return `Err("check: --repair cannot be combined with --chain (repair operates on a single image)".into())`. (ii) Capture the post-repair result counts at `run_check` scope: in the vCPU loop where `check_passed` is set (~6980), also record `corruptions`, `refcount_errors`, `chain_errors`, `leaks`, `total_errors`, and the `FLAG_NOT_SUPPORTED` bit into `run_check`-scoped variables (e.g. an `Option<CheckExit>` struct). (iii) Replace the tail `if !check_passed { return Err(...) }` (~7078) with a qemu-style mapping over the captured counts: clean (`corruptions == 0 && refcount_errors == 0 && chain_errors == 0 && leaks == 0`) ⇒ `Ok(())` (exit 0); leaks-only (those three error classes 0 but `leaks > 0`) ⇒ `std::process::exit(3)`; otherwise ⇒ `std::process::exit(2)`. Keep `Err` (exit 1) for genuine VM/I-O failures (the `vm_error` path stays). For `FLAG_NOT_SUPPORTED`, match `qemu-img check` empirically (run `qemu-img check` on a raw file to confirm its exit code and message; mirror it). Build `cargo build -p vmm`. Opus: the 3-way mapping must use the *post-repair* counts the guest already decremented, must not regress the clean/error paths the integration suite exercises, and `process::exit` vs `Err` interplay (Err always maps to 1) needs care. |
| 6b | medium | sonnet | none | Repair-result rendering + `--repair=all` warnings in `src/vmm/src/main.rs`. (i) In `print_check_result` (human, ~7086), after the errors/leaks block, add a repair section: if `result.repaired_leaks > 0` print `"Repaired {n} leaked clusters."`; if `result.repaired_refcounts > 0` print `"Corrected {n} refcount(s)."`; if `FLAG_REPAIR_INCOMPLETE` is set print `"Repair did not complete; some issues remain (re-run or use qemu-img)."`. Match qemu-img's phrasing where reasonable. (ii) In `print_check_result_json`, add matching keys (e.g. `"repaired-leaks"`, `"repaired-refcounts"`, `"repair-incomplete"`) without breaking the existing schema the parity tests read (append fields; keep existing ones). (iii) Strengthen `CheckArgs.repair`'s doc comment so `--help` clearly states `--repair=all` is **lossy and rewrites metadata in place; back up valuable images first**. (iv) In `run_check`, when `matches!(args.repair, Some(RepairMode::All))`, print one stderr line: `eprintln!("warning: --repair=all rewrites image metadata in place; back up valuable images first");` before launching the guest. Build `cargo build -p vmm`. Sonnet: well-scoped rendering following the existing `print_check_result` patterns. |
| 6c | medium | sonnet | none | Verify and commit. `cargo build -p vmm`; `make instar` (confirm `check.bin` byte-identical — host-only change); `make test-rust`; **`make test-integration`** for the check suites (`tests/test_check_formats.py`, `tests/test_check_chain.py`) — these exercise the exit-code paths; investigate any failure (a test asserting the old exit `1` should be updated to the qemu 0/2/3 convention and the change noted). `make lint`; `pre-commit run --all-files`. Then exit-code smokes with the freshly-built `instar`: (a) **clean image** `instar check` ⇒ exit 0; (b) **leaky image** (reuse a diverged-snapshot fixture, which instar reports as leaks) `instar check` ⇒ exit 3; (c) a structurally-corrupt fixture if available ⇒ exit 2; (d) `instar check --repair=all` on a clean image prints the stderr warning and exits 0; (e) `instar check --repair --chain …` ⇒ exits non-zero with the reject message. Compare (a)/(b) exit codes against `qemu-img check` on the same files. Stage and present ONE commit (6a+6b). The message explains: host CLI polish — qemu-parity 0/2/3 exit codes mapped from the post-repair result, repair-result rendering (human + JSON), the `--repair=all` destructive warning, and the `--repair`+`--chain` reject; host-only so `check.bin` is unchanged; the exit-code change matches qemu for plain `check` too and the existing suite (which asserts `!= 0`, not `== 1`) stays green. |

## Agent guidance

### Execution model

Sub-agents implement in the `check-repair` worktree; the
management session reviews the diff, runs the gates + smokes, and
commits. This phase is host-only and lower-risk than 4/5, so the
sub-agents may work directly in the tree (no per-step worktree).

### Model and effort notes

- **6a is high-effort opus**: the exit-code semantics must match
  qemu and not regress the integration suite; `process::exit`
  vs `Err` and the post-repair-count mapping are the traps.
- **6b is medium sonnet**: additive rendering + warnings following
  existing patterns.
- **6c is medium sonnet**: scripted verify + the exit-code smokes,
  which must each compare against qemu-img.

### Management session review checklist

- [ ] Exit codes: clean ⇒ 0, leaks-only ⇒ 3, corruptions/refcount/
      chain ⇒ 2; genuine failures still ⇒ 1; computed from the
      **post-repair** counts.
- [ ] `make test-integration` check suites pass (or breakages are
      old-convention assertions, updated + noted).
- [ ] Repair section renders only when something was repaired or
      `INCOMPLETE`; JSON keys are additive (parity tests still
      parse).
- [ ] `--repair --chain` rejected with a clear message before any
      device open.
- [ ] `--repair=all` prints the destructive warning; `--help` text
      documents it.
- [ ] `check.bin` byte-identical (host-only change).
- [ ] `make lint`, `pre-commit` clean.

## Administration and logistics

### Success criteria

* `instar check` exit codes match `qemu-img check` (0/2/3) for
  clean / leaks / errors, including the post-repair state under
  `--repair`.
* Repair results render in human and JSON output; `--repair=all`
  warns; `--repair --chain` is rejected.
* `make instar` (`check.bin` unchanged), `make test-rust`,
  `make test-integration` (check suites), `make check-binary-sizes`,
  `make lint`, `pre-commit` all pass.
* Lands in one commit on the `check-repair` branch.

### Future work created by this phase

- **Wire the repair counters to the host** so the renderer can
  print "Repaired N leaked clusters" / "Corrected N refcounts"
  like qemu-img. Needs three small changes (a guest+proto change,
  so a separate commit/phase — not host-only): add the three
  `repaired_*` fields to the proto `CheckResultMessage`, copy them
  in `src/core/src/serial.rs`'s `send_check_result` conversion,
  and render them in `print_check_result[_json]`. Until then a
  successful repair prints only the post-repair clean state with
  no "what was fixed" line. Tracked here; a good candidate to fold
  into phase 8 (integration) or its own short follow-up.
- A `--repair`-specific JSON schema aligned with `qemu-img check
  --output=json`'s repair fields, if a consumer needs exact
  parity.

### Bugs fixed during this work

- **Repair counters are not on the wire (scope correction).**
  Phase 1 added `repaired_leaks` / `repaired_refcounts` /
  `repaired_corruptions` to the `#[repr(C)] shared::CheckResult`,
  but the guest→host **protobuf** `CheckResultMessage`
  (`crates/guest-protocol/proto/guest.proto`, the serial
  conversion in `src/core/src/serial.rs`, and the host parser)
  carries only the older 12 fields — *not* the repair counters.
  Only `flags` (hence `FLAG_REPAIR_INCOMPLETE`) reaches the host.
  So the `"Repaired N leaked clusters."` / `"Corrected N
  refcount(s)."` human lines and the `repaired-leaks` /
  `repaired-refcounts` JSON keys are **impossible host-only** —
  rendering them needs a guest+proto change, which this phase
  forbids (`check.bin` must stay byte-identical). Implemented the
  achievable subset: the `FLAG_REPAIR_INCOMPLETE` message + a
  `repair-incomplete` JSON key. The **exit codes are unaffected**
  — `leaks`/`corruptions`/`refcount_errors`/`chain_errors` *are*
  on the wire and the guest decrements them post-repair, so 0/2/3
  reflect the post-repair state correctly. See Future work.
- **`not_supported` exit code matches qemu at 63, not 0.** The
  plan guessed "likely 0"; empirically `qemu-img check` on a raw
  file prints "does not support checks" and exits **63**
  (`EXIT_NOT_SUPPORTED`). instar previously exited 0 for such
  images; this phase mirrors qemu's 63. Verified safe: the check
  integration suite (`test_check_formats.py`, `test_check_chain.py`)
  passed 76/2-skipped, and the raw/`--unsafe-quirks` tests assert
  on JSON content, not the exit code.

### Documentation index maintenance

This is a phase plan, not a master plan: **not** added to
`order.yml`. The master plan's phase-6 row is updated to "Landed"
once the commit is in.

### Back brief

Before executing any step, back brief the operator on your
understanding — especially that the exit-code change applies to
all `check` (qemu parity), is computed from the post-repair
counts, and must keep the existing integration suite green.
