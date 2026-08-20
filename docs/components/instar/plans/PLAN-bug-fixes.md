# Triage and fix the June 2026 fuzzer bug backlog

## Status: Complete

Phases 1-3: `bbfdfc9`, `514c52a`, `a54cef8`.

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
file per detailed phase. These separate files are named for the
master plan, in the same directory as the master plan, and
simply have `-phase-NN-descriptive` appended before the `.md`
file extension. Tracking of these sub-phases is done via the
table in the Execution section below.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a single
commit. Each commit should be self-contained: it should build,
pass tests, and have a clear commit message explaining what
changed and why.

## Situation

A second wave of `security-audit` GitHub issues has accumulated
since the [Fuzzing bug backlog](/components/instar/plans/PLAN-fuzzing-bugs/) plan drained
the May 2026 set. The coverage-guided fuzzers
(`PLAN-coverage-fuzzing.md`) and the manual `PLAN-snapshot`
pre-push audit have together left **10 open bug issues** as of
2026-06-13. Nine carry the `autofix-failed` label — the automated
fixer could not land a working patch in two attempts — and the
tenth (#365) was filed by hand from the snapshot audit because it
needs genuine root-causing, not a bounds check.

Triage shows the 10 issues collapse to **three distinct root
causes**, each localised to a single code site.

### Issue inventory and categorisation

Run `gh issue list --repo shakenfist/instar --state open` to
refresh the list. As of 2026-06-13 the open bug issues group as:

#### Category A — `fuzz_create_emitters` invariant-3 overflow, Fixed VHD (7 issues)

| Field | Value |
|-------|-------|
| Target | `fuzz_create_emitters` |
| Panic site | `src/fuzz/fuzz_targets/fuzz_create_emitters.rs:225` — invariant 3: `assert!(plan.total_metadata_bytes.checked_add(plan.minimum_file_size).is_some(), ...)` |
| Format hit | **VHD, subformat Fixed** (every reproducer decodes to `target_sel=2`, `vhd_sub=Fixed`, `virtual_size` ≈ `u64::MAX`) |
| Root cause | The Fixed-VHD branch of `plan_vhd` (`src/crates/create/src/lib.rs:845`) places the 512-byte footer at `byte_offset = opts.virtual_size`, so `minimum_file_size = virtual_size + 512`. It has **no upper bound on `virtual_size`**. For `virtual_size` near `u64::MAX`, `total_metadata_bytes (512) + minimum_file_size` overflows `u64`, tripping invariant 3. The Dynamic-VHD branch already rejects oversize inputs (its `u32` BAT-entry count overflows and returns `CreateError::Overflow`); the Fixed branch has no parallel guard. |

Decoded reproducers (first byte `% 4 = 2` → VHD; byte 5 `% 2 = 1` → Fixed;
bytes 8..16 little-endian → `virtual_size`):

| Issue | virtual_size | Notes |
|-------|--------------|-------|
| #367 | `0xfffffffffffffd80` | |
| #363 | `0xfffffffffffffd00` | |
| #362 | `0xfffffffffffffd00` | autofix attempt-2 proposed the correct cap (`0xff00_0000 * 512`) but did not land |
| #361 | `0xfffffffffffffdff` | |
| #357 | `0xfffffffffffffdff` | |
| #355 | `0xfffffffffffffdc1` | |
| #353 | `0xfffffffffffffc02` | |

All seven are the **same bug** with different fuzzer-minimised
inputs. One fix closes all seven.

#### Category B — `fuzz_resize_planners` VHDX sequence-number overflow (2 issues)

| Field | Value |
|-------|-------|
| Target | `fuzz_resize_planners` |
| Panic site | `src/crates/resize/src/vhdx.rs:248:34` — `build_header(active_buf, opts.current_sequence_number + 2)` |
| Root cause | `current_sequence_number` is a `u64` taken **verbatim from the parsed VHDX header** (the fuzzer sets it directly from 8 input bytes — `fuzz_resize_planners.rs:292`). The VHDX shrink planner increments it unchecked at `vhdx.rs:247` (`+ 1`) and `:248` (`+ 2`); the grow planner does the same at `:162-163`. When the header's sequence number is at or near `u64::MAX`, the `+ 2` overflows and panics in debug builds. |

Issue list: #360 (`vhdx.rs:248:34`), #354 (`vhdx.rs:248:34`). Same
bug; one fix closes both. Note there are **four** unchecked
increment sites total (grow `:162-163`, shrink `:247-248`) — the
fix must cover all of them, not only the line the reproducer
happens to hit.

#### Category C — `resize --shrink` corrupts qcow2 with sub-byte refcount widths (1 issue)

| Field | Value |
|-------|-------|
| Issue | #365 (filed by hand from the `PLAN-snapshot` pre-push audit) |
| Symptom | `instar resize --shrink` on a qcow2 with `refcount_bits` 1/2/4 produces an image that `qemu-img check` reports as corrupt (referenced clusters left at `refcount=0`), while exiting `0`. `refcount_bits=16` is unaffected. Silent success over a corrupted image. |
| Already fixed | The first of two defects — the shared sub-byte refcount accessors packing entries MSB-first instead of qemu's LSB-first — was fixed in commit `f3d2a49`. The corruption reproduces **identically** after that fix, so a second, independent width assumption remains. |
| Root cause (suspected, not yet confirmed) | A second width assumption in the shrink refcount staging/rebuild path. `plan_shrink` computes `entries_per_refblock` correctly from `refcount_bits`, so the suspect is elsewhere — a refblock-entry write at a hardcoded 16-bit stride, or refcount-table regeneration math. The garbage values `qemu-img check` reports (e.g. `0x3F00`, `0x1111`) look like multi-bit writes landing in sub-byte refblocks. **Requires root-causing.** |

#### Cross-cutting `autofix-failed`

Nine issues (#353, #354, #357, #360, #361, #362, #363 carry the
label; #355 and #367 are the most recent two and have not yet been
through autofix) had the workflow give up after two attempts. For
Category A the autofix repeatedly proposed a `virtual_size`
overflow guard *and a unit test* but never landed a working patch
— the proposed guard did not actually gate the Fixed-VHD path the
reproducer exercises, or its own validation still crashed. These
are resolved by the corresponding category fix below; we are not
retrying autofix per-issue.

## Mission and problem statement

Land fixes for all three categories so that:

1. The reproducer for every referenced issue stops crashing under
   `cargo fuzz run <target> <reproducer>` (reproducers are the
   Base64 blobs in each issue body; reconstruct them under
   `src/fuzz/artifacts/<target>/` if not already committed).
2. A sustained `cargo fuzz run <target> -- -max_total_time=600`
   campaign against `fuzz_create_emitters` and
   `fuzz_resize_planners` (with the existing corpus plus these
   reproducers) finds no new crashes.
3. The #365 reproduction (`refcount_bits` 1/2/4 shrink) either
   produces a `qemu-img check`-clean image or fails loudly with a
   non-zero exit — never silent success over corruption.
4. All 10 referenced issues are closed with a commit cross-reference.

A "fix" for an autofix-failed issue is the same fix as for the
underlying category — we are not retrying the autofix workflow on
a per-issue basis.

## Open questions

1. **Category A — cap value.** qemu's `vpc.c` rejects VHDs larger
   than `VHD_MAX_SECTORS` (`0xFF000000` sectors = 2040 GiB). The
   Fixed branch should reject `virtual_size` above that same cap.
   *Recommendation:* match qemu's `0xFF000000 * 512` and return
   `CreateError::InvalidVirtualSize`, mirroring the existing
   `virtual_size == 0` rejection a few lines up. Apply the cap
   before the subformat split so it covers Dynamic too (Dynamic
   currently only rejects via the incidental `u32` BAT overflow,
   which is a much higher and less principled bound). Confirm the
   exact constant against `qemu-img create -f vpc` boundary
   behaviour during the phase.

2. **Category B — checked, saturating, or validate-and-reject.**
   The sequence number is monotonically incremented on every
   header write; near `u64::MAX` it is already pathological.
   *Recommendation:* reject up-front — if `current_sequence_number`
   is within 2 of `u64::MAX`, return `ResizeError::Overflow` once
   at the top of the planner rather than sprinkling `checked_add`
   at four call sites. A real VHDX never reaches that sequence
   number; an image that claims to has a corrupt header. Confirm
   how the existing VHDX parser surfaces the sequence number and
   whether any other planner path consumes it.

3. **Category C — root-cause-and-fix vs. gate.** Two postures: (a)
   find and fix the second width assumption so sub-byte shrink
   produces clean images, or (b) refuse `refcount_bits != 16` for
   `resize --shrink` loudly, the posture the snapshot mutating
   modes already take. *Recommendation:* spend a bounded
   investigation budget (one high-effort session) attempting (a);
   if the second assumption is not cleanly isolable, fall back to
   (b) — a loud refusal is strictly better than silent corruption,
   and sub-byte refcounts on a shrink are a narrow, non-default
   real-world case. Decide at the phase, not now.

4. **Fuzz coverage gap.** The differential resize fuzzer's
   `op_resize` picker never overrides `refcount_order`, which is
   why #365 escaped. Adding a `refcount_bits` dimension to its
   image generation would cover this class going forward. *Should
   that land in this plan or a fuzzing follow-up?* *Recommendation:*
   fold the fuzzer dimension into Phase 3 so the gate/fix and the
   coverage that guards it ship together.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Category A: Fixed-VHD `virtual_size` overflow guard | [PLAN-bug-fixes-phase-01-fixed-vhd-overflow.md](/components/instar/plans/PLAN-bug-fixes-phase-01-fixed-vhd-overflow/) | Complete (commit `bbfdfc9`) |
| 2. Category B: VHDX resize sequence-number overflow | [PLAN-bug-fixes-phase-02-vhdx-resize-seqnum.md](/components/instar/plans/PLAN-bug-fixes-phase-02-vhdx-resize-seqnum/) | Complete (commit `514c52a`) |
| 3. Category C: qcow2 shrink sub-byte refcount corruption | [PLAN-bug-fixes-phase-03-qcow2-shrink-subbyte-refcount.md](/components/instar/plans/PLAN-bug-fixes-phase-03-qcow2-shrink-subbyte-refcount/) | Complete (commit `a54cef8`) |

Phases are independent and can land in any order. The
recommended order is by ascending risk and difficulty: Phase 1
(7 duplicate issues, a single localised planner guard, lowest
risk) clears most of the board; Phase 2 (2 issues, a single
localised guard) is next; Phase 3 (1 issue, but requires guest
shrink-path root-causing and a fix-vs-gate decision) is the
hardest and lands last.

## Agent guidance

### Execution model

Per `~/.claude/CLAUDE.md` operator preference and the precedent of
[PLAN-fuzzing-bugs.md](/components/instar/plans/PLAN-fuzzing-bugs/), implementation work
for Phases 1 and 2 may be done **in the management session** —
they are small, well-understood, localised changes. Phase 3 should
use a **sub-agent in a worktree** for the root-cause investigation
(it is exploratory and may produce a discarded branch if the
fix-vs-gate decision lands on "gate"). Each phase still carries an
effort and model recommendation.

### Planning effort

This master plan was created at high effort. Phase planning effort
is called out per phase: Phases 1 and 2 are medium (localised,
well-understood); Phase 3 is high (guest shrink-path
investigation, format-spec interpretation, a design decision).

### Step-level guidance

Each phase plan includes a step table with effort, model, isolation,
and brief.

### Management session review checklist

After each phase:

- [ ] The reproducer for at least one issue in the category no
      longer crashes (`cd src/fuzz && cargo fuzz run <target>
      artifacts/<target>/<reproducer>`); for Phase 3, the #365
      shell reproduction is `qemu-img check`-clean or exits
      non-zero.
- [ ] `make instar` builds and `make lint` is clean.
- [ ] Guest binaries pass `make check-binary-sizes` (384KB limit
      per operation).
- [ ] `make test-rust` and the relevant `make test-integration`
      targets pass.
- [ ] `pre-commit run --all-files` passes.
- [ ] Each closed GitHub issue links back to the commit that
      resolved it (`Closes #N` in the commit, or
      `gh issue close -c "Fixed in <sha>"`).

## Administration and logistics

### Success criteria

* `make instar` builds and `make lint` is clean.
* Guest binaries pass `make check-binary-sizes` (384KB limit).
* All Rust unit tests pass (`make test-rust`).
* All Python integration tests pass (`make test-integration`).
* `pre-commit run --all-files` passes.
* A sustained `cargo fuzz run fuzz_create_emitters -- -max_total_time=600`
  and the same for `fuzz_resize_planners` find no new crashes with
  the existing corpus plus these reproducers.
* The #365 shell reproduction across `refcount_bits` 1/2/4/16
  yields either a `qemu-img check`-clean image or a non-zero exit
  for the sub-byte widths — never exit 0 over a corrupt image.
* A regression test pins each fix (the byte-exact reproducer for
  A and B; the shell reproduction or a unit test for C).
* All 10 GitHub issues listed above are closed with a commit
  cross-reference.
* `docs/plans/index.md` and `docs/plans/order.yml` include this
  master plan.

### Future work

* Add a `refcount_bits` dimension to the differential resize
  fuzzer's image generation (see Open question 4) — folded into
  Phase 3 unless deferred.
* The Fixed-VHD `virtual_size` cap (Phase 1) is the principled
  bound the Dynamic branch lacks (Dynamic only rejects via the
  incidental `u32` BAT overflow). Consider hoisting the cap above
  the subformat split so both branches share one explicit check;
  Phase 1 should do this if low-risk.
* The four unchecked sequence-number increments in `vhdx.rs`
  (Phase 2) are a pattern worth a lint/grep sweep — check whether
  the create-side VHDX writer (`plan_vhdx`) or the snapshot crate
  have similar unchecked monotonic-counter arithmetic.

### Bugs fixed during this work

This section will list the commits that close each category once
the phases land.

* **Category A — Fixed-VHD `virtual_size` overflow (7 issues):**
  #353, #355, #357, #361, #362, #363, #367.
* **Category B — VHDX resize sequence-number overflow (2 issues):**
  #354, #360.
* **Category C — qcow2 shrink sub-byte refcount corruption (1 issue,
  commit `a54cef8`):** #365. The root cause turned out to be broader
  than the resize shrink path: two shared write-side width assumptions
  in `crates/qcow2::create` (`build_header` hardcoded `refcount_order`
  to the 16-bit default; `set_refcount_to_one` packed sub-byte widths
  MSB-first instead of qemu's LSB-first). Both are reached by the shrink
  header rebuild *and* by plain `create`, so the same fix also resolved
  a latent **`instar create -o refcount_bits=N` corruption** for
  `refcount_bits != 16`. The integration suites' known-divergence skips
  for the qcow2 rb-1/rb-8/rb-64 create and resize cases were removed
  (they now run live and match qemu), and the differential fuzzer's
  create and resize pickers gained a `refcount_bits` dimension.

### Documentation index maintenance

When the first phase of this plan lands:

* Add a row to `docs/plans/index.md` under *Master plans* with
  date 2026-06-13, a link to this file, the intent line, status
  "In progress", and the three phase links.
* Add `PLAN-bug-fixes.md: June 2026 fuzzer bug backlog` to
  `docs/plans/order.yml` (master plans only — phase files are not
  added to `order.yml`).

When all phases are complete, update the status in `index.md` to
*Complete*.

### Back brief

Before executing any step of this plan, back-brief the operator on
your understanding of the plan and how the work aligns with it. In
particular, re-run the `gh issue list --repo shakenfist/instar
--state open` query before opening each phase to confirm the issue
list has not drifted (new fuzzer issues may have been filed).
