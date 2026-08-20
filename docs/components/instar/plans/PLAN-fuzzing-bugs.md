# Triage and fix the standing fuzzing-bug backlog

## Status: Complete

Phases 1-5.

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on external concepts (QCOW2, VMDK, VHD/VHDX, LUKS, KVM, virtio,
disk image formats), research as needed to give a confident
answer. Flag any uncertainty explicitly rather than guessing.

All planning documents go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the overall system structure
(host VMM, KVM guest, call table, device emulation).
Consult `AGENTS.md` for build commands, project conventions,
code organisation, and the security model summary. Consult
`docs/` for format-specific documentation (`docs/qcow2/`,
`docs/raw/`, etc.) and `docs/commentary/` for architectural
decisions and design rationale.

When we get to detailed planning, I prefer a separate plan
file per detailed phase. These separate files should be named
for the master plan, in the same directory as the master
plan, and simply have `-phase-NN-descriptive` appended before
the `.md` file extension. Tracking of these sub-phases is
done via the table in the Execution section below.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

The coverage-guided fuzzing (`PLAN-coverage-fuzzing.md`),
differential fuzzing (`measure` phase 9, `create` phase 10,
`resize` phase 12), and fuzz-autofix workflow (`PLAN-fuzz-autofix.md`)
have together filed **44 open GitHub issues** labelled
`security-audit`. Eleven of those issues carry the additional
`autofix-failed` label — the automated fixer was unable to land a
working patch in two attempts.

The autofix workflow tells us the easy ones are already fixed.
The standing backlog is what needs human (or higher-effort
Claude) attention. Triage shows the 44 issues collapse to **five
distinct root causes** that span both the parsers/emitters under
fuzz and the differential-fuzz harness itself.

### Issue inventory and categorisation

Run `gh issue list --repo shakenfist/instar --search "fuzz" --state open`
to refresh the list. As of 2026-05-27 the open issues group as:

#### Category A — coverage-fuzz panics (32 issues across 3 sites)

| ID | Target | Panic site | Likely root cause |
|----|--------|------------|-------------------|
| A1 | `fuzz_create_emitters` | `src/crates/create/src/lib.rs:526:26` (inside `plan_vmdk`) | Arithmetic in the VMDK capacity calculation panics for adversarial `(virtual_size, grain_size)` tuples. Line 526 is `let capacity_bytes = opts.virtual_size.div_ceil(grain_size_bytes) * grain_size_bytes;` — `div_ceil` panics on a zero divisor, and the multiplication overflows `u64` for large virtual sizes. The fuzz harness already validates the structural plan invariants; the planner must instead return `CreateError::InvalidGrainSize` / `CreateError::Overflow` rather than panic. |
| A2 | `fuzz_measure_scan` | `fuzz_targets/fuzz_measure_scan.rs:74` — `assert!(s.allocated_bytes <= s.virtual_size, ...)` | QCOW2 `scan_allocation` produces an `AllocationSummary` where `allocated_bytes > virtual_size` for some malformed (but parseable) headers — typically L1/L2 tables that point past the virtual size, or pre-allocated clusters in a sparse image whose `size` field underflows. The invariant is correct; the scanner needs to cap or reject. |
| A3 | `fuzz_measure_calc` | `fuzz_targets/fuzz_measure_calc.rs:144` — `assert!(m.required.checked_add(m.fully_allocated).is_some(), ...)` | The target-format calculators (`measure_qcow2` / `measure_vhd` / `measure_vhdx` / `measure_vmdk`) sometimes return outputs whose `required + fully_allocated` overflows `u64`. The calculators should detect the overflow and surface `MeasureError::Overflow` instead. |

**Issue lists (current):**

- A1 (7): #339, #331, #328, #322, #318, #314, #309
- A2 (10): #338, #330, #321, #317, #313, #308, #304, #297, #295, #292
- A3 (15): #337, #333, #329, #327, #320, #316, #312, #307, #305, #303, #296, #294, #291, #290, #289

#### Category B — differential-fuzz divergences (12 issues across 2 patterns)

| ID | Pattern | Root cause |
|----|---------|------------|
| B1 | `instar measure` rejects VPC (fixed-VHD) source images as `unsupported format`; `qemu-img measure` succeeds. `instar_rc=1`, `qemu_rc=0`. | `src/operations/measure/src/main.rs:detect_and_scan` calls `detect_format_from_header` on the first sector. Fixed VHDs carry no header magic (the `conectix` cookie lives only in the trailing-sector footer), so detection returns `Raw`. For the seeds that hit this issue, the planning chain produces a fixed-VHD source where the first sector is not all zeros (e.g. carries a partition table), causing `detect_format_from_header` to mis-classify in a way that leads `detect_and_scan` to bail with `None` → `MeasureError::InvalidSize`. Fix: have `detect_and_scan` read the trailing footer when the leading-sector classification yields `Raw`, mirroring the lookup that `instar info` and `instar check` already perform via `detect_vhd_from_footer`. |
| B2 | `qemu-img` times out (`TIMEOUT after 30s`) on resize-shrink; instar succeeds. The harness records this as `exit_code_divergence` (`instar_rc=0`, `qemu_rc=-1`). | qemu-img is known to hang for some adversarial qcow2 shrink inputs with `cluster_size=512` and `lazy_refcounts=on`. This is an upstream-qemu pathology, not an instar bug. The differential-fuzz harness (`scripts/differential-fuzz.py`) should classify external-tool timeouts as `inconclusive` and skip filing them rather than emit `exit_code_divergence`. |

**Issue lists (current):**

- B1 (9): #335, #325, #324, #323, #319, #311, #310, #306, #293
- B2 (3): #336, #334, #315

#### Cross-cutting `autofix-failed`

These 11 issues (#333, #328, #322, #318, #314, #309, #305, #297, #295, #292, #290) had the autofix workflow throw in the towel after two attempts. They will be resolved by the corresponding category fix above — autofix struggled because the change requires planner-or-calculator-level invariant work, not a one-line bounds check, and because the second attempt produced an empty diff.

## Mission and problem statement

Land fixes for all five categories so that:

1. The five reproducer corpora (committed under
   `src/fuzz/artifacts/<target>/` and referenced in each issue)
   stop crashing under `cargo fuzz run <target>`.
2. New coverage-fuzz panics in those targets stop being filed
   (verified by running a sustained `--max_total_time=600`
   campaign against each target locally with the current corpus
   plus the reproducers from the closed issues).
3. New differential-fuzz divergences matching patterns B1 and
   B2 stop being filed (verified by re-running the harness
   against the seeds called out in those issues).
4. All 44 referenced issues are closed with a commit-link
   cross-reference.

A "fix" for an autofix-failed issue is the same fix as for the
underlying category — we are not retrying the autofix workflow
on a per-issue basis.

## Open questions

1. **B1 scope:** should `instar measure` add full VPC source
   support (matching `instar info` / `instar check`), or should
   the differential-fuzz harness skip source formats outside
   measure's documented surface? Recommendation: extend measure
   to detect fixed VHDs via the trailing footer — the parsing
   infrastructure already exists in `src/crates/vhd/` and the
   guest op already imports it. The fuzz harness's view of
   "supported source formats" should match documented behaviour,
   not be narrowed to dodge a bug.
2. **B2 scope:** the harness change is a one-liner but should it
   also retroactively reclassify the three filed issues as
   `inconclusive` rather than closing them as "not a bug"?
   Recommendation: close as "not a bug" with a comment that
   references the harness change; the harness change prevents
   recurrence.
3. **A2 cap-vs-reject:** when `scan_allocation` would report
   `allocated > virtual`, should it cap at `virtual_size` or
   return `None` (which produces `MeasureError::InvalidSize`)?
   Recommendation: cap. The qemu-img behaviour for the same
   image is to cap; differential fuzz would then accept the
   reading.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Category A1: `plan_vmdk` capacity panic | [PLAN-fuzzing-bugs-phase-01-create-emitters.md](/components/instar/plans/PLAN-fuzzing-bugs-phase-01-create-emitters/) | Complete (commit `0220ae9`) |
| 2. Category A2: qcow2 `scan_allocation` invariant break | [PLAN-fuzzing-bugs-phase-02-measure-scan.md](/components/instar/plans/PLAN-fuzzing-bugs-phase-02-measure-scan/) | Complete (commit `6de9687`) |
| 3. Category A3: measure calculator overflow | [PLAN-fuzzing-bugs-phase-03-measure-calc.md](/components/instar/plans/PLAN-fuzzing-bugs-phase-03-measure-calc/) | Complete (commit `b4e312d`) |
| 4. Category B1: vhd/vhdx/vmdk `allocated_bytes` clamp | [PLAN-fuzzing-bugs-phase-04-measure-fixed-vhd.md](/components/instar/plans/PLAN-fuzzing-bugs-phase-04-measure-fixed-vhd/) | Complete (commit `bed14fc`); root cause turned out to be unclamped block-count overshoot in scan_allocation, not fixed-VHD detection — phase plan still names the original hypothesis |
| 5. Category B2: differential-fuzz timeout classification | [PLAN-fuzzing-bugs-phase-05-diff-fuzz-timeouts.md](/components/instar/plans/PLAN-fuzzing-bugs-phase-05-diff-fuzz-timeouts/) | Complete (commit `71e3e33`) |

Phases are independent and can land in any order. I suggest
landing them in the listed order because phases 1-3 carry the
highest count of issues and phases 4-5 require harness-side
changes that we want exercised in CI before claiming the backlog
is drained.

## Agent guidance

### Execution model

Per `~/.claude/CLAUDE.md` operator preference, implementation
work is done **in the management session, not via sub-agents** —
the sub-agent execution model from the template is overridden
for this plan. Each phase still carries an effort recommendation
so the operator can pick the right model for a fresh session if
needed.

### Planning effort

The master plan was created at high effort. Phase planning
effort is called out per phase. Implementation effort is also
called out per phase.

### Step-level guidance

Each phase plan includes a step table with effort, model, and
brief, but the operator runs the steps directly.

### Management session review checklist

After each phase:

- [ ] The reproducer for at least one issue per category no
      longer crashes (`cd src/fuzz && cargo fuzz run <target>
      artifacts/<target>/<reproducer>`).
- [ ] `make instar` builds and `make lint` is clean.
- [ ] Guest binaries pass `make check-binary-sizes` (384KB limit
      per operation).
- [ ] `make test-rust` and the relevant `make test-integration`
      targets pass.
- [ ] `pre-commit run --all-files` passes.
- [ ] Each closed GitHub issue links back to the commit that
      resolved it (`gh issue close -c "Fixed in <sha>"`).

## Administration and logistics

### Success criteria

* `make instar` builds and `make lint` is clean.
* Guest binaries pass `make check-binary-sizes` (384KB limit).
* All Rust unit tests pass (`make test-rust`).
* All Python integration tests pass (`make test-integration`).
* `pre-commit run --all-files` passes.
* For each category, a sustained `cargo fuzz run <target>
  -- -max_total_time=600` (10 minutes per target) finds no new
  crashes with the existing corpus plus the reproducers from
  this backlog.
* A re-run of `python3 scripts/differential-fuzz.py --seed
  <seed> --iterations N --fail-fast` for each of the seeds
  cited in B1 and B2 issues completes without recording the
  same divergence.
* All 44 GitHub issues listed above are closed with a commit
  cross-reference.
* `docs/plans/index.md` and `docs/plans/order.yml` include this
  master plan.

### Future work

* The fuzz-autofix workflow handled 0 of these issues despite
  attempting many of them. After this plan lands, consider a
  retro on the autofix complexity guardrails (turn limit, file
  count, single-crate scope) — five sample issues per category
  on hand make for a good evaluation set.
* The differential-fuzz harness has no current concept of
  source-format support gates. If we ship more measure source
  formats in future (e.g. VDI), the harness should consult the
  same support matrix rather than infer it from exit-code
  divergence.
* `fuzz_measure_scan.rs:74` and `fuzz_measure_calc.rs:144`
  encode parser invariants in the harness. Consider promoting
  these to debug-asserts inside the relevant `measure_*`
  functions so the invariants are checked in unit tests too,
  not only under libfuzzer.

### Bugs fixed during this work

All 44 open `security-audit` GitHub issues at the start of this
plan are closed by the five phase commits. Auto-close via the
`Closes #N` keywords in each commit message; one issue (#315)
was miscategorised in the initial triage and is fixed by phase 4
but not referenced in `bed14fc` — it will be closed manually
post-merge with a pointer to `bed14fc`.

* **A1 — `fuzz_create_emitters` panic (7 issues, commit `0220ae9`):**
  #309, #314, #318, #322, #328, #331, #339.
* **A2 — `fuzz_measure_scan` invariant break (10 issues, commit `6de9687`):**
  #292, #295, #297, #304, #308, #313, #317, #321, #330, #338.
* **A3 — `fuzz_measure_calc` overflow (15 issues, commit `b4e312d`):**
  #289, #290, #291, #294, #296, #303, #305, #307, #312, #316,
  #320, #327, #329, #333, #337.
* **B1 — vhd/vhdx/vmdk `allocated_bytes` overshoot
  (9 issues commit `bed14fc` + 1 manual close):** #293, #306,
  #310, #311, #319, #323, #324, #325, #335, plus #315
  (miscategorised in original triage; same root cause).
* **B2 — qemu-img timeout reclassification (2 issues, commit `71e3e33`):**
  #334, #336.

### Documentation index maintenance

When the first phase of this plan lands:

* Add a row to `docs/plans/index.md` under *Master plans* with
  date 2026-05-27, link to this file, the intent line, status
  "In progress", and the five phase links.
* Add `PLAN-fuzzing-bugs.md: Fuzzing bug backlog` to
  `docs/plans/order.yml` (master plans only — phase files are
  not added to `order.yml`).

When all phases are complete, update the status in `index.md`
to *Complete*.

### Back brief

Before executing any step of this plan, back-brief the operator
on your understanding of the plan and how the work aligns with
it. In particular: confirm the issue list for each phase has not
drifted (new issues may have been filed) by re-running the
`gh issue list` query before opening the phase.
