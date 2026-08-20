# Format coverage expansion

## Status: Complete

All 7 phases, finished 2026-07-20. Closed the input-side
format-coverage gap against qemu-img's real 14-format roster. Four new
`no_std` guest crates (`vdi`, `parallels`, `qcow1`, `dmg`) with
coverage-guided and differential fuzz coverage graduated VDI,
Parallels, QCOW1 and DMG from detect+info-only to full
convert/compare/dd/bench read support; detection parity closed against
oslo.utils' roster plus four formats oslo never detected; QED received
a recorded refusal-as-policy decision rather than a read path;
`test_info_safe` grew 580 to 954 passing scenarios. Along the way,
fixed the pre-existing #444 defect (detect-only formats silently read
as raw), a live QCOW1-misdetected-as-QCOW2 defect, the never-consumed
`INFO_RESULT_FLAG_ENCRYPTED` flag, and three instar-testdata defects.

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

All planning documents should go into `docs/plans/`.

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
the `.md` file extension. Tracking of these sub-phases is done
via the table in the Execution section below.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

With `bench` landed, instar implements all 15 qemu-img
subcommands — the *subcommand* parity roster is closed. The
remaining parity axis is *format* coverage, and it has never
had a tracking document. This plan is that document.

`qemu-img --help` advertises ~40 "supported image formats",
but that list mixes three different kinds of block driver:
protocol drivers (`file`, `nbd`, `http`, `ssh`, `iscsi`,
`rbd`, `gluster`, `nfs`, `nvme`, ...), filter drivers
(`blkdebug`, `compress`, `copy-on-read`, `quorum`,
`throttle`, ...), and actual on-disk image format drivers.
Only the last group is in instar's mission. There are 14 of
them in a current QEMU (verified against qemu-img 10.0.11):

| Format | qemu-img | instar today |
|--------|----------|--------------|
| raw | read/write/create | full support |
| qcow2 | read/write/create | full support |
| vmdk | read/write/create | full support (see subformat note) |
| vpc (VHD) | read/write/create | full support |
| vhdx | read/write/create | full support |
| luks | read/write/create | full support (v1/v2, info + convert with decryption) |
| vdi | read/write/create | detect + info only |
| qcow (v1) | read/write/create (deprecated) | detect + info only |
| qed | read/write/create (deprecated) | detect + info only |
| parallels | read/write/create | **not detected** |
| bochs | read-only | **not detected** |
| cloop | read-only | **not detected** |
| dmg | read-only | **not detected, unmentioned anywhere in the repo** |
| vvfat | read-only pseudo-format | **not detected** |

("read-only" statuses verified empirically: `qemu-img create
-f {bochs,cloop,dmg,vvfat}` fails with "Format driver does
not support image creation" on qemu-img 10.0.11.)

Current tracking state of these gaps:

* `docs/format-coverage.md` acknowledges the Parallels /
  Bochs / cloop *detection* gaps (rows marked **No**) and
  notes that test images for all three already exist in
  instar-testdata ("in testdata, not tested"). But that
  document's charter is **oslo.utils format_inspector
  parity**, which instar already meets in full — none of
  these three is detected by oslo either, so by that
  document's own success criterion nothing further is owed.
* VDI, QED, and QCOW1 are documented as detection/info-only
  (`docs/format-coverage.md`, `docs/quirks.md`), and the
  convert input-format table simply omits them with no
  "not yet" marker. Current state is recorded; future work
  is not.
* DMG appears nowhere in the repository: not in docs/,
  README, ARCHITECTURE.md, CHANGELOG, code, tests, or
  `src/shared/src/format_detection.rs`. QEMU has shipped a
  read-only DMG (Apple Disk Image) driver for years — its
  main real-world use is `qemu-img convert` of macOS
  installer/recovery images — parsing the UDIF/BLKX ("mish")
  chunk table and decompressing zlib (UDZO) chunks, plus
  bzip2 (UDBZ) and lzfse (ULFO) when built with those
  libraries.
* No `PLAN-*.md` covers new-format work, and no GitHub issue
  tracks format coverage (checked 2026-07-17).

Why instar should care: hostile-input, compressed,
offset-table formats like DMG are precisely the case where
instar's KVM sandbox is a genuine advantage over qemu-img.
Read-only input support (detect → info → convert-from) fits
the existing architecture without needing a write path, and
matches how these images arrive in practice (something a
user downloaded and wants converted to qcow2/raw).

## Mission and problem statement

Close instar's format-coverage gap against qemu-img's real
image-format roster on the **input side**, in a consistent,
tracked manner:

1. **Detection parity.** `instar info` should detect every
   on-disk image format qemu-img can probe: add Parallels,
   Bochs, cloop, and DMG magic detection to
   `src/shared/src/format_detection.rs`, with the staged
   testdata images finally exercised by tests.
2. **Info support.** For each newly detected format, `info`
   reports at minimum the format name and virtual size
   (mirroring the existing VDI/QED handling in
   `src/operations/info/`), with qemu-img cross-validation.
3. **Convert-from (read path) for formats that matter.**
   Full input support — convert, compare, and the other
   read-consuming subcommands as applicable — for:
   * **VDI** (dynamic and static; VirtualBox images are
     still commonly encountered),
   * **Parallels** (v2 "WithoutFreeSpace"; v1 if cheap),
   * **QCOW1** (deprecated but still in archives; read-only
     input),
   * **DMG** (read-only input; zlib/UDZO chunks in v1, see
     Open questions for bzip2/lzfse).
4. **Recorded refusals for formats that don't.** Bochs,
   cloop, and vvfat get detection (where feasible) plus a
   clean, tested "detected but unsupported" refusal — the
   same stance the codebase already takes for QED — and an
   explicit rationale in docs. QED's own fate is an open
   question below.
5. **Documentation.** Either widen `docs/format-coverage.md`
   with an explicit qemu-img-parity axis or add a sibling
   document, so future gaps land in a table instead of being
   rediscovered by archaeology.

### Explicitly out of scope

* **Write/create/output support for any new format.** The
  convert output roster (raw, qcow2, vmdk, vpc, vhdx) is
  unchanged by this plan. qemu-img can create vdi /
  parallels / qcow1 / qed images, but demand for *writing*
  those formats is negligible; revisit only on real demand.
* **vvfat as anything more than detection-or-refusal.** It
  is not a file format — it synthesises a FAT filesystem
  from a host directory — and has no sensible meaning as
  convert input for a sandboxed converter.
* **VMDK subformat expansion** (e.g. `twoGbMaxExtentSparse`
  output, ESX `vmfs` variants). Different axis; deserves its
  own plan if wanted.
* **Protocol and filter drivers** (nbd, http, luks-via-URI,
  blkdebug, ...). Out of mission entirely.

## Open questions

1. **QED: read support or principled refusal?** oslo.utils
   bans QED outright; QEMU deprecates it but still reads and
   writes it; instar currently detects it and refuses.
   Options: (a) keep the refusal and document it as policy,
   (b) add a read path for parity. Leaning (a) — the format
   was never widely deployed — but if archives of QED images
   surface in practice, (b) is a small format (it is
   essentially a simplified qcow2 without refcounts).
   **RESOLVED 2026-07-19 by phase 6: (a), refusal as
   policy** — see
   [PLAN-format-coverage-phase-06-qed.md](/components/instar/plans/PLAN-format-coverage-phase-06-qed/)
   for the decision record, with one correction to this
   question's framing: QEMU does NOT formally deprecate QED
   (no deprecated.rst entry, no runtime warning, create
   still works on 10.2.0) — the refusal is instar's own
   scope choice, aligned with oslo.utils' explicit ban and
   nil demand, with recorded revisit criteria and a
   path-(b) sketch preserved in the phase plan. **Phase 6
   executed this decision on 2026-07-20**: QED-named refusal
   pins now cover every subcommand that previously lacked
   one, and a stale, unconsumed testdata baseline set was
   retired (commits `3fd48e6` in instar, `cecb16565a` in
   instar-testdata).
2. **DMG detection is trailer-based.** DMG has no magic at
   offset 0 — the UDIF "koly" signature lives in the last
   512 bytes of the file. `detect_format_from_header()`
   currently sees only a header-prefix buffer, and QEMU
   itself mostly relies on the `.dmg` extension (its probe
   function is weak). Do we extend the detection API to
   optionally read the trailer, detect by extension like
   qemu, or both? This is the main architectural question in
   the plan and needs settling in phase 1.
3. **DMG compression codecs for v1.** zlib (UDZO) and
   uncompressed/zero chunk types cover the overwhelming
   majority of real DMGs and the guest already links a
   no_std inflate for qcow2/vmdk. bzip2 (UDBZ) and lzfse
   (ULFO) would each need a new no_std decompressor inside
   the 768KB guest binary cap. Proposal: v1 = zlib +
   uncompressed + zero/ignore chunk types, with typed
   refusals naming the unsupported codec for UDBZ/ULFO, and
   codec expansion recorded as future work.
4. **Which subcommands gain each new input format?**
   Minimum is info + convert + compare. map / measure / dd
   take format-specific extent iterators and allocation
   scanners; deciding per-format whether that work is in
   scope belongs to each format's phase plan. (dd notably
   auto-probes its input, so at least a clean refusal is
   needed there regardless.)
5. **Testdata provenance.** parallels-v1, parallels-v2,
   empty.bochs, and simple-pattern.cloop already exist in
   instar-testdata but are untested; DMG and VDI-static
   fixtures may need generating (macOS `hdiutil` is not
   available in CI — real-world DMG samples plus
   qemu-created ones from `qemu-img convert -O dmg`... which
   does not exist, so DMG fixtures must come from archived
   real images or a small generator script). The baseline
   generator (`generate-baselines.py`) lives in
   instar-testdata; new fixtures follow that pattern.
6. **Differential fuzzing scope.** For formats qemu-img
   reads (all of these), the differential fuzzer can compare
   convert output against qemu-img convert. Do we gate each
   format's phase on differential coverage (as every recent
   subcommand plan has), or accept coverage-guided fuzzing
   of the parsers alone for the read-only-input formats?
   Leaning: differential for VDI/Parallels/QCOW1/DMG — the
   whole point is parity — parser fuzzing only for the
   detection-only formats.

## Execution

Proposed phase decomposition. Each phase gets its own
detailed plan file before implementation begins; the table
is the tracking source of truth.

| Phase | Plan | Status |
|-------|------|--------|
| 1. Detection + info parity (Parallels, Bochs, cloop, DMG; settle the trailer-probe question) | [PLAN-format-coverage-phase-01-detection.md](/components/instar/plans/PLAN-format-coverage-phase-01-detection/) | Complete (commits 3c0fff1..5042d74 + docs commit) |
| 2. VDI convert-from (dynamic + static read path, new `src/crates/vdi/`) | [PLAN-format-coverage-phase-02-vdi-read.md](/components/instar/plans/PLAN-format-coverage-phase-02-vdi-read/) | Complete (commits 6cd14b5..cf213ed + docs commit) |
| 3. Parallels convert-from (v2 read path, new `src/crates/parallels/`) | [PLAN-format-coverage-phase-03-parallels-read.md](/components/instar/plans/PLAN-format-coverage-phase-03-parallels-read/) | Complete (commits 3f43472..f2bacf4 + docs commit) |
| 4. QCOW1 convert-from (read path, new `src/crates/qcow1/`; fixes the misdetection-as-qcow2 defect) | [PLAN-format-coverage-phase-04-qcow1-read.md](/components/instar/plans/PLAN-format-coverage-phase-04-qcow1-read/) | Complete (commits 23b240f..efdc42e + docs commit) |
| 5. DMG convert-from (BLKX chunk table + zlib chunks, new `src/crates/dmg/`; EIO-parity error semantics, typed codec/capacity refusals) | [PLAN-format-coverage-phase-05-dmg-read.md](/components/instar/plans/PLAN-format-coverage-phase-05-dmg-read/) | Complete (commits 71a20d9..9d8111c + docs commit) |
| 6. QED decision: refusal as policy (Open question 1 RESOLVED; per-op pins + testdata reconciliation + decision record) | [PLAN-format-coverage-phase-06-qed.md](/components/instar/plans/PLAN-format-coverage-phase-06-qed/) | Complete (2026-07-20; commits 3fd48e6 instar, cecb16565a testdata) |
| 7. Docs: qemu-img-parity axis in format-coverage.md, README/ARCHITECTURE/CHANGELOG updates | [PLAN-format-coverage-phase-07-docs.md](/components/instar/plans/PLAN-format-coverage-phase-07-docs/) | Complete (2026-07-20; commit `de1c3bc` (7a, the parity axis) + the 7b close-out commit) |

Sequencing rationale: phase 1 is cheap, self-contained, and
settles the one architectural question (trailer probing)
that phase 5 depends on. Phases 2–5 are ordered by expected
real-world demand (VDI > Parallels > QCOW1 > DMG) but are
largely independent and could be reordered or parallelised.
Each read-path phase follows the established per-format
pattern: no_std parser crate under `src/crates/` with unit
tests, guest-side integration into the convert/compare
readers, host-side probe/CLI wiring, testdata fixtures,
qemu-img cross-validation baselines, integration tests,
coverage-guided fuzzing of the parser, and differential
fuzzing against qemu-img (per Open question 6).

Per-phase constraints that apply throughout:

* Guest binaries must stay under the 768KB per-operation
  cap (`make check-binary-sizes`; convert currently sits at
  ~303KB); DMG's inflate reuse and any new decompressor
  need size budgeting up front.
* Format parsers are `no_std` and panic-free; all offsets
  and lengths from untrusted headers are bounds-checked
  before use (the existing qcow2/vmdk crates are the
  pattern).
* Every new input format gets adversarial fixtures
  (truncated tables, offsets past EOF, overlapping chunks,
  compression bombs) alongside the happy-path images.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never
in the management session. The management session (this
conversation) is reserved for planning, review, and
decision-making. This keeps the management context lean
and avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step
   with the brief from the plan, at the recommended
   effort level and model.
3. **Review** the sub-agent's output in the management
   session. Check the actual files — the sub-agent's
   summary describes what it intended, not necessarily
   what it did.
4. **Fix or retry** if the output is wrong. Diagnose
   whether the brief was insufficient (improve it) or
   the model was too light (upgrade it), then re-run.
5. **Commit** once the management session is satisfied
   with the result.

This applies to all steps, including high-effort ones.
If a sub-agent can't succeed even with a detailed brief
and the right model, that's a signal the brief needs
improving, not that the management session should do
the implementation itself.

Use `isolation: "worktree"` for sub-agents when the
change is risky or experimental. The worktree is
discarded if the output is unsatisfactory. For safe,
well-understood changes, sub-agents can work directly
in the main tree.

### Planning effort

The master plan itself should always be created at
**high effort** — it requires broad codebase
understanding, cross-referencing multiple source files,
and making judgment calls about scope and sequencing.

Each phase plan should specify the recommended effort
level for planning that phase. Phases involving deep
protocol research, format-spec interpretation, or
architectural decisions (call-table changes, new
operations, new shared crates, security boundary
changes) should be planned at high effort. Phases that
are mechanical or follow well-established patterns can
be planned at medium effort.

For this plan specifically: phases 1 and 5 (detection
API change, DMG chunk-table research) warrant high
effort; phases 2–4 follow the established per-format
read-path pattern and can likely be planned at medium
effort with good briefs; phases 6–7 are mostly
decision-recording and documentation.

### Step-level guidance

Each phase plan should include a table like this:

```
| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a   | medium | sonnet | none     | One-sentence summary of what to do and which files to touch |
| 1b   | high   | opus   | worktree | Why this needs high effort: requires understanding X to do Y |
```

**Effort levels:**
- **high** — Requires reading multiple files, making
  judgment calls, understanding non-obvious invariants,
  or researching external references (format specs,
  qemu-img source, KVM/virtio docs). The sub-agent
  needs to think carefully about edge cases.
- **medium** — The plan provides enough context that the
  sub-agent can follow a clear brief. May need to read
  a few files but the approach is well-defined.
- **low** — Purely mechanical changes (rename, reformat,
  add a log line). The brief is a complete instruction.

**Model choice:** The planner should recommend which
model is best suited for each step. This is a judgment
call, not a rigid rule — the right model depends on what
the step requires, not on whether it's "planning" or
"implementation".

- **opus** — Best for steps that require deep reasoning,
  cross-file architectural understanding, subtle
  correctness judgment, or complex format/protocol
  research. Also appropriate for intricate implementation
  where getting it wrong would be costly to debug
  (e.g. cluster-table writers, refcount management,
  call-table changes that bridge VMM and guest).
- **sonnet** — Good default for well-briefed
  implementation work. Faster and cheaper than opus.
  Works well when the plan front-loads the research
  and the brief is detailed enough that the agent
  doesn't need to make broad judgment calls.
- **haiku** — Suitable for purely mechanical tasks:
  search-and-replace, adding log lines, running
  commands. The brief must be a near-complete
  instruction.

The model choice interacts with effort level and brief
quality. A detailed brief compensates for a lighter
model — sonnet at medium effort with a thorough brief
often matches opus at medium effort with a vague brief.
The planner's job is to write briefs good enough that
the recommended model can succeed.

Note: the model also determines the context window
(opus has 1M tokens, sonnet and haiku have 200K). Steps
that require holding many files in context simultaneously
may need opus for that reason alone, even if the
reasoning itself is straightforward. Format-conversion
work in particular tends to span the source format
parser, the destination format writer, the call table,
and the host-side glue at the same time.

**When in doubt, skew to the more capable model.**
Saving money only matters if the outcome is still
acceptable. A failed or low-quality implementation
wastes more time (and therefore more money) than using
a heavier model would have cost. Only recommend a
lighter model when you are confident the brief is
detailed enough for it to succeed.

**Brief for sub-agent:** This is the key field. Write it
as if briefing a colleague who has never seen the
codebase. Include: what to change, which files to touch,
what patterns to follow, and any non-obvious constraints
(memory layout, the 768KB guest binary cap, the
no-`std` requirement of the format crates, the call
table boundary). The better the brief, the lower the
effort level needed and the lighter the model that can
succeed.

A good brief front-loads the research the planner already
did, so the implementing agent doesn't repeat it. For
example, instead of "add tests for the QCOW2 L2 parser",
write "add tests for `parse_l2_entry()` in
`src/crates/qcow2/src/lib.rs`. Use the adversarial
fixtures in `instar-testdata/adversarial/qcow2/` (cluster
boundary edges, OFLAG_COMPRESSED set with extended L2
cluster, refcount underflow). The function takes
`(entry: u64, cluster_bits: u32)` and returns
`Option<L2Entry>`."

### Management session review checklist

After a sub-agent completes, the management session
should verify:

- [ ] The files that were supposed to change actually
      changed (read them, don't trust the summary).
- [ ] No unrelated files were modified.
- [ ] `make instar` builds and `make lint` is clean.
- [ ] Guest binaries pass `make check-binary-sizes`
      (768KB limit per operation).
- [ ] `make test-rust` and the relevant
      `make test-integration` targets pass.
- [ ] `pre-commit run --all-files` passes.
- [ ] The changes match the intent of the brief — not
      just syntactically correct but semantically right.
- [ ] Commit message follows project conventions
      (including the Co-Authored-By line with model,
      context window, effort level, and other settings).

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* `make instar` builds and `make lint` is clean. **Satisfied
  cumulatively across phases 1-7**: every guest-integration
  and crate step's brief gated its commit on `make instar` +
  `make lint` clean (e.g. phase 1 step 2a, phase 2 step 2b,
  phase 3 step 3b, phase 4 step 4b, phase 5 step 5b), recorded
  in each phase plan's Findings; no outstanding lint/build
  failure was ever left behind.
* Guest binaries pass `make check-binary-sizes` (768KB limit).
  **Satisfied**: phases 2-5 each required `make
  check-binary-sizes` clean before landing and reported the
  per-binary delta (phase 4's Findings: "All four deltas stayed
  well within the 768 KB per-binary cap"); the tightest budget
  discussion — DMG's ~1.25 MiB chunk-table scratch region —
  was sized against the compile-time layout assert in phase 5
  step 5b and stayed within cap.
* All Rust unit tests pass (`make test-rust`). **Satisfied**:
  every crate/guest-integration step across phases 1-6 required
  `make test-rust` clean, including the new `no_std` unit-test
  suites in `src/crates/vdi`, `src/crates/parallels`,
  `src/crates/qcow1`, and `src/crates/dmg` (findings in each
  phase's 2a/3a/4a/5a and 2b/3b/4b/5b steps).
* All Python integration tests pass (`make test-integration`).
  **Satisfied**, recorded zero-fail per phase (do not
  re-run): phase 1 `test_info_safe` 580/580; phase 2
  `test_info_safe` 628/628 plus `test_oslo_crossval` 221
  passed; phase 3 eight suites zero-fail including
  `test_adversarial` 83 and `test_info_safe` 800; phase 4
  eight consumer suites clean, `test_info_safe` 898/0; phase 5
  `test_info_safe` 954/0, `test_convert` 251/0, `test_compare`
  65/0, `test_dd` 45/0 (full sequential matrix, zero failures
  across every consumer suite); phase 6 ten suites zero-fail
  (`check_formats` 77, `map` 100, `measure` 289, `bench` 81,
  `resize` 98, `rebase` 28, `commit` 26, `amend` 28, `snapshot`
  94, `bitmap` 53) with `test_info_safe` confirmed unchanged at
  954/954 after the testdata reconciliation.
* `pre-commit run --all-files` passes. **Satisfied**: every
  phase landed its commits under the project's standing
  pre-commit-clean convention (Design/Agent-guidance
  constraint applied throughout); no phase's Findings record an
  outstanding pre-commit failure.
* `instar info` detects every on-disk image format a current
  qemu-img can probe (or the phase-1 plan records why a
  specific format's probe is not reproducible host-side).
  **Satisfied**: phase 1 added Parallels, Bochs, cloop, and DMG
  detection (confirmed by `test_info_safe` 580/580 including
  the four new formats), closing the gap this plan's Situation
  table identified. vvfat is the one qemu-advertised format
  with no on-disk container to detect — the phase-7a axis
  documents that rationale rather than adding a detection path
  (see the "vvfat" subsection of `docs/format-coverage.md`).
* VDI, Parallels, QCOW1, and DMG images convert correctly to
  every existing output format, cross-validated against
  `qemu-img convert` output. **Satisfied** by phases 2-5
  respectively: each phase's integration-test step (2e/3e/4e/5e)
  cross-validates convert-to-raw/qcow2/vpc byte parity against
  `qemu-img convert` across the full safe-fixture set, recorded
  zero-fail in the suite counts above.
* Bochs, cloop, vvfat (and QED, per the phase-6 decision)
  produce clean, tested, documented refusals rather than
  misdetection as raw. **Satisfied for QED by phase 6**
  (2026-07-20, commits `3fd48e6` instar / `cecb16565a`
  testdata): every subcommand lacking a QED-named refusal
  pin now has one, and the decision record lives in
  `docs/quirks.md` and
  [PLAN-format-coverage-phase-06-qed.md](/components/instar/plans/PLAN-format-coverage-phase-06-qed/).
  **Satisfied for Bochs/cloop by phase 1**: detect + info only,
  with the issue-#444 gate producing a clean, pinned refusal for
  convert/compare/dd/bench (see the qemu-img parity axis's Note
  11 in `docs/format-coverage.md`, sourced to quirks.md phase 1).
  **Satisfied for vvfat by phase 7a**: the axis's "vvfat"
  subsection records that vvfat is a directory-backed pseudo-
  format with no on-disk single-file container, so there is
  nothing for instar to detect or refuse — satisfied by
  documented rationale rather than code, re-verifying `qemu-img
  create -f vvfat`'s own creation refusal on qemu-img 10.0.11.
* New format parsing lives in shared crates under
  `src/crates/`, `no_std`-compatible for guest use, with
  coverage-guided fuzz targets. **Satisfied**: `src/crates/vdi`,
  `src/crates/parallels`, `src/crates/qcow1`, and
  `src/crates/dmg` shipped in phases 2-5, each `no_std` with its
  own coverage-guided fuzz targets (`fuzz_vdi_header` /
  `fuzz_vdi_bat`, `fuzz_parallels_header` /
  `fuzz_parallels_bat`, `fuzz_qcow1_header` / `fuzz_qcow1_table`,
  `fuzz_dmg_table` / `fuzz_dmg_chunk`) plus differential-fuzz
  coverage against qemu-img, recorded in each phase's 2f/3f/4f/5f
  Findings with zero crashes and zero divergences over the
  ~200-iteration forced burn-ins.
* The staged instar-testdata images (parallels-v1,
  parallels-v2, empty.bochs, simple-pattern.cloop) are
  exercised by tests, and new fixtures exist for VDI-static
  and DMG. **Satisfied**: phase 1 wired the four staged images
  into `test_info_safe`; phase 2 added the VDI-static and
  additional VDI fixtures (`vdi-static-data`,
  `vdi-data-dynamic`, `vdi-odd-size`, `vdi-bmap-past-eof`, plus
  five malformed adversarial fixtures); phase 5 added the DMG
  fixture set (`dmg-simple`, `dmg-mixed`, `dmg-multipart`,
  `dmg-rsrc-fork`, and 12 more per the Format Detection
  Comparison table in `docs/format-coverage.md`).
* `docs/format-coverage.md` (or a sibling document) tracks
  format coverage against qemu-img's real format-driver
  roster, not just oslo.utils. **Satisfied by phase 7a**
  (2026-07-20, commit `de1c3bc`): the "qemu-img parity axis"
  section — a consolidated, fully-sourced op × format matrix
  (read-side, in-place, and output-side tables, 16 notes, and
  the vvfat subsection) — widens the document's charter to both
  oslo.utils parity and qemu-img roster coverage.
* `ARCHITECTURE.md`, `README.md`, `AGENTS.md`, and
  `CHANGELOG.md` have been updated as needed. **Satisfied**:
  each of phases 1-6 shipped its own docs step updating
  `ARCHITECTURE.md` (the four new crates, chain-reader feature
  dispatch) and `CHANGELOG.md` (six phase entries; phase 7 adds
  none per Decision 2, docs-only changes get no entry).
  `README.md`'s Supported Formats list and `AGENTS.md` were
  current after each per-phase step except two spots phase 7b
  fixed: `AGENTS.md`'s stale Supported Formats section (still
  named the pre-programme roster) is now the real write/luks/
  read-only-input/detection-only/QED breakdown with a pointer to
  the parity axis, and `README.md`'s "Initial target formats"
  heading (stale wording) plus its VDI line (missing "bench" —
  confirmed against `tests/test_bench.py`'s
  `test_bench_vdi_simple`, which is a live rc-0 parity pin) are
  corrected to match the parallels/qcow/dmg lines.

### Programme retrospective

Closed out 2026-07-20 (phase 7, docs). All seven phases are Complete.
The durable artifact for the finished programme is the qemu-img
parity axis in `docs/format-coverage.md`; the per-phase Findings
sections in each `PLAN-format-coverage-phase-NN-*.md` remain the
historical record, and this section is the one-page end state.

#### The seven phases

| Phase | Outcome |
|-------|---------|
| 1. Detection + info parity | Added Parallels, Bochs, cloop, and DMG magic detection to `src/shared/src/format_detection.rs` plus `info` parsing for all four; `test_info_safe` grew 580/580. Found and fixed the pre-existing #444 defect (detect-only formats silently read as raw by convert/compare/dd) with a central `discover_backing_chain` gate. |
| 2. VDI convert-from | New `src/crates/vdi/` no_std reader (dynamic + static, bmap lookup, capacity-clamped zero-fill); graduated VDI out of the #444 gate for convert/compare/dd/bench. `test_info_safe` 628/628, `test_oslo_crossval` 221 passed, zero-crash/zero-divergence fuzzing. |
| 3. Parallels convert-from | New `src/crates/parallels/` reader (both magics, v2 "WithoutFreeSpace"); cluster-size info plumbing; graduated for convert/compare/dd/bench. Eight consumer suites zero-fail (`test_info_safe` 800), zero-crash/zero-divergence fuzzing. Recorded a real qemu regression (`parallels_check_duplicate` assertion crash on 10.x) as the reason instar's own `check` continues to refuse Parallels. |
| 4. QCOW1 convert-from | New `src/crates/qcow1/` reader (backing chains, raw-deflate compressed clusters). Found and fixed a live QCOW1-misdetected-as-QCOW2 defect (commit `c421f75`) and the never-consumed `INFO_RESULT_FLAG_ENCRYPTED` (commit `467d24a`). Ordered the reader-arm commit strictly before the detection fix to close a silent-raw hazard window. Eight suites clean, `test_info_safe` 898/0. |
| 5. DMG convert-from | New `src/crates/dmg/` reader (koly trailer, XML-plist and resource-fork chunk tables, zlib/raw/zero/ignore chunk codecs, EIO-parity error semantics for truncated raw spans). Found the qemu DMG zero-chunk NULL-dereference crash (upstream bug, all qemu-img 6.0.0-10.2.0) and shipped a clean typed refusal instead of mirroring it. `test_info_safe` 954/0, `test_convert` 251/0, `test_compare` 65/0, `test_dd` 45/0 — zero failures across every consumer suite. |
| 6. QED decision | Resolved Open question 1: refusal as policy, not read support (a) — nil real-world demand plus oslo.utils' explicit ban, with a path-(b) reader sketch preserved for a future revisit. Added QED-named refusal pins for the ten ops that lacked one (commit `3fd48e6`); corrected the plan's own premise mid-step (the "unconsumed" qed baseline claim was wrong) and retired only the genuinely unconsumable check/compare baseline trees (`cecb16565a` in instar-testdata) after empirical confirmation. `test_info_safe` unchanged at 954/954. |
| 7. Docs | The qemu-img parity axis (`de1c3bc`, 7a) — a consolidated, fully-sourced op × format matrix (read-side, in-place, output-side, 16 notes, vvfat rationale) with zero conflicts against six phases of recorded quirks.md facts and three management-independent re-verifications. Consistency fixes (`AGENTS.md`, `README.md`) and this close-out (7b). |

#### The shipped capability

Four new `no_std` guest-format crates (`src/crates/vdi`,
`src/crates/parallels`, `src/crates/qcow1`, `src/crates/dmg`), each
with coverage-guided fuzz targets and differential-fuzz coverage
against qemu-img, graduating VDI, Parallels, QCOW1, and DMG from
detect+info-only to full read support (convert / compare / dd /
bench) — closing the input-side format-coverage gap the plan set out
to close. Detection parity against oslo.utils' roster is closed (all
formats oslo detects, instar also detects, plus four more oslo does
not: Parallels, Bochs, cloop, DMG). One format (QED) received a
recorded policy refusal rather than a read path. `test_info_safe`
grew from 580 to 954 passing scenarios across the programme, and
every phase's full consumer-suite matrix ran zero-fail at each
landing (recorded per-phase above; not re-run for this close-out).
The qemu-img parity axis in `docs/format-coverage.md` is the durable
artifact that replaces per-op archaeology with a single sourced
matrix for future gaps.

#### Bugs fixed along the way

* **#444** (detect-only formats silently read as raw by
  convert/compare/dd) — FIXED by phase 1 (commit `83a9e5c`): a
  central gate in `discover_backing_chain` refuses unrecognised
  formats instead of falling through to raw.
* **QCOW1 misdetected as QCOW2** — FIXED by phase 4 (commit
  `c421f75`): detection is now version-aware (`QFI\xfb` + version 1
  routes to the new QCOW1 reader; any other version keeps the QCOW2
  route).
* **`INFO_RESULT_FLAG_ENCRYPTED` never consumed** — FIXED by phase 4
  (commit `467d24a`): both host `info` emitters now print `encrypted:
  yes` / `"encrypted": true`, gated off for bare LUKS to keep those
  goldens byte-identical.
* **Three instar-testdata defects**, found and fixed alongside the
  code work (see the master plan's "Bugs fixed during this work"
  section below for the full record): the parallels driver was
  missing from the 6.0.0-6.2.0 static qemu-img builds; the committed
  `profiles/` and `version-map.json` were stale relative to `raw/`;
  and `detect-profiles.py` was corrupting regenerated profiles by
  comparing mismatched id granularities. (Phase 7's planning brief
  referred to "four instar-testdata defects" in passing; the
  master plan's own "Bugs fixed" section records three code-adjacent
  testdata defects plus, separately, phase 6's QED baseline
  reconciliation — a scoped cleanup executed per the phase-6
  decision, not a defect fix. This close-out cites the section as
  written rather than inflating the count.)

#### Verification posture

Every phase's read-path work was cross-validated against
`qemu-img convert`/`compare`/`dd`/`bench` for byte parity on the
supported surface, with recorded, footnoted divergences where instar
deliberately refuses (map/measure on the four new formats) or where a
genuine qemu-img behavioural difference was found (the Parallels
`check` assertion crash, the DMG zero-chunk NULL-dereference crash).
Every new parser crate carries coverage-guided fuzz targets plus
differential-fuzz coverage against qemu-img (zero crashes, zero
divergences across every phase's burn-in). The qemu-img parity axis
(phase 7a) is the first document to consolidate every recorded
divergence — sourced to a quirks.md section, an existing table, or a
fresh 2026-07-20 measurement against qemu-img 10.0.11 — into one op ×
format matrix; management independently re-verified three of the
fresh measurements (VHD `check`, VHDX `map`, VDI `bench`) and found
zero conflicts with the axis as written.

### Future work

We should list obvious extensions, known issues, unrelated bugs
we encountered, and anything else we should one day do but have
chosen to defer to here so that we don't forget them.

* QED read support (phase 6's path-(b) sketch: a qcow1-class
  reader — 68-byte LE header, two-level L1/L2 cluster-offset
  tables, no compression/encryption, in-header backing name).
  Deliberately deferred, not abandoned — the phase-6 decision
  is refusal as policy, revisit only on a real user request to
  read QED input or QED images surfacing in a served workload
  (see `docs/plans/PLAN-format-coverage-phase-06-qed.md`'s
  Decision section for the full revisit criteria and sketch).
* DMG bzip2 (UDBZ), lzfse (ULFO), and ADC chunk codec decode
  support (deferred from phase 5 per Open question 3; instar
  issues typed refusals naming the code instead, and qemu's
  own support is compile-flag dependent across the version
  matrix anyway, so there is no single parity target — see
  `docs/quirks.md` "Format-coverage phase 5").
* Streaming decompression for DMG chunks that exceed instar's
  bounded-memory staging caps (1 MiB plist region, 32768-chunk
  table, 4096-sector per-chunk staging): phase 5's typed
  capacity refusal (pinned by `dmg-overcap-chunk`) stands in;
  revisit only if real-world images exceed the 2 MiB per-chunk
  staging cap in practice.
* Write/create/output support for VDI or Parallels, if real
  demand appears.
* VMDK subformat expansion (twoGbMaxExtentSparse output,
  ESX variants) — separate plan.
* `map` / `measure` / `dd` support for the new input
  formats, where each phase plan chose to defer it.
* Wire DMG koly-trailer probing into the in-place-op
  detection paths (host `probe_*_target` prefix probes, the
  guest map/measure ops, `resize`, and `check`'s own format
  dispatch), so DMG is refused/recognised there like
  bochs/cloop/parallels instead of passing through as (or
  being refused while named) raw. Phase 5 graduated DMG to a
  full read format for convert/compare/dd/bench but
  deliberately left this bullet open — map/measure/resize
  pins are unchanged and `check` still names the format "raw"
  (see `docs/quirks.md` "Format-coverage phase 5" and "DMG
  Pass-Through as Raw in the In-Place Ops").
* `instar check` support for VDI (phase 2 future work: qemu-img
  `check` validates the VDI block map; unconsumed check baselines
  already exist in instar-testdata from `generate-baselines.py`).
* Parallels format extensions / dirty bitmaps: phase 3's reader
  refuses any non-zero `ext_off` at init rather than parsing the
  format extension qemu reads read-only, since no shipped or
  creatable fixture needs it today (deliberate divergence, see
  `docs/quirks.md` "Format-coverage phase 3"). Revisit if a real
  need for extension/dirty-bitmap data appears.
* Report the qemu DMG zero-chunk NULL-dereference crash upstream:
  a DMG with a valid koly trailer but zero parsed chunks (bad mish
  magic, broken base64, or no `<data>` blocks) segfaults every
  qemu-img from 6.0.0 through host 10.0.11 on any read (`info` is
  unaffected). Found by phase-5 planning's empirical pass
  (2026-07-19); instar's phase-5 reader refuses the empty table
  cleanly instead of mirroring the crash. The reproducer has since
  shipped as the `dmg-empty-table` instar-testdata fixture
  (`skip_qemu_img`, since qemu crashes on convert) — use it directly
  when filing the upstream report rather than reconstructing one.
* Report the qemu `parallels_check_duplicate` assertion crash
  (10.2.0's `qemu-img check` asserts on an out-of-image BAT entry
  that 6.0.0 reports cleanly) upstream to the qemu project; this is
  also why `instar check` continues to refuse Parallels rather than
  mirroring qemu-img's check support.
* The `profile-8-1-0` baseline split introduced by phase 3 (to
  record qemu 8.1.0-8.1.5's past-EOF-BAT open-refusal regression
  for `parallels-bat-past-eof`) received copies of `profile-8-0-0`'s
  hand-maintained LUKS goldens so pre-split coverage wasn't lost;
  any future profile split should follow the same precedent — carry
  over the neighbouring profile's hand-authored LUKS goldens rather
  than regenerating them.
* QCOW1 (qcow) AES decryption (crypt_method=1): phase 4's reader
  refuses encrypted qcow1 cleanly at open, matching keyless qemu's
  own refusal, rather than implementing AES-128-CBC decryption;
  instar already has the crypt_method=1 machinery from QCOW2 to
  reuse if real demand appears (see `docs/quirks.md`
  "Format-coverage phase 4").
* `map` / `measure` support for qcow1: qemu-img actually supports
  both against a qcow1 source; instar's refusals are a deliberate,
  recorded divergence (already covered by the general "`map` /
  `measure` / `dd` support for the new input formats" bullet above,
  which now also applies to qcow1's map/measure gap specifically —
  `dd` itself is already supported for qcow1).
* A `--no-commit`-style output-type-limiting flag for
  `instar-testdata`'s `generate-baselines.py`: recorded as
  recommended in the phase-2 findings and manually worked around
  in phases 3-6, but never centrally tracked until now.
* Report oslo.utils' qcow1-misdetected-as-qcow2 behaviour
  upstream (phase-4 finding; parallel to the two qemu
  upstream-report items already listed above).
* Extract a shared rounds-protocol-length helper in
  `src/vmm/src/main.rs` so the human and JSON info emitters
  can't drift (pre-push audit, code-quality advisory; mirrors
  the existing `should_emit_encrypted_line` pattern).
* Evaluate a shared resolve-and-read-span helper across all six
  chain-reader arms (VHD/VMDK/VDI/Parallels/Qcow1/Dmg) in
  `src/crates/qcow2` (pre-push audit advisory; the duplication
  predates this branch).
* Tidy `src/operations/info`'s `probe_dmg_trailer` to use
  `checked_mul` like the dmg crate's `read_koly` (pre-push audit
  security-informational; operands are host-controlled, not
  attacker-reachable).
* Update `.github/workflows/functional-tests.yml`'s inline
  cargo-test list and `coverage-fuzz.yml`'s `TARGETS` array
  mechanism so they can't drift from the Makefile/fuzz crate
  again (being fixed point-in-time by this audit; the structural
  drift-proofing is future work).

### Bugs fixed during this work

This section should list any bugs we encounter during
development that we fixed. You should also scan the relevant
github bug tracker to see if there are any directly related
bugs that we should either resolve as part of this master
plan, or at least be aware of when planning. (A scan on
2026-07-17 found no existing format-coverage issues; the
open issues are fuzz crashes, consistency checks, and qcow2
operation bugs.)

* **CONFIRMED pre-existing defect
  ([#444](https://github.com/shakenfist/instar/issues/444)):
  detect-only formats are silently read as raw by
  convert/compare/dd.** Found during phase-1 planning
  (2026-07-17) by code reading and confirmed the same day by
  step 1a's empirical pin: those ops probe input via
  `discover_backing_chain` → guest info, and
  `chain::ImageFormat::from_str` (`src/vmm/src/chain.rs:50`)
  maps unrecognised format strings to `Unknown`, which the
  guest chain reader's default arm reads as raw sectors —
  `instar convert` of a QED image emits its container bytes
  zero-padded to the header-declared virtual size (byte-
  verified), contradicting the documented "detects it and
  refuses" stance for QED. ISO flows through the same path
  but is exempted by management decision: its raw read is
  semantically correct and matches qemu-img. **Fixed by
  83a9e5c** (step 3b): a single central gate in
  `discover_backing_chain` refuses with a typed
  `ChainError::UnsupportedInputFormat` when the guest-reported
  format maps to `chain::ImageFormat::Unknown` and is not
  `raw`/`unknown`/`iso`, covering top-level images and every
  mid-chain backing position; iso keeps its exempted raw
  pass-through. No existing test depended on the silent-raw
  behaviour. Findings in
  [PLAN-format-coverage-phase-01-detection.md](/components/instar/plans/PLAN-format-coverage-phase-01-detection/).
* **FIXED (phase 4, commit `c421f75`): real QCOW1 images were
  misdetected as qcow2.** Found during phase-4 planning
  (2026-07-18) and empirically pinned by the management
  session: `detect_format_from_header` checked the 4-byte
  `QFI\xfb` magic against qcow2 FIRST and never consulted the
  version field, so every real qcow1 image (whose magic IS
  `QFI\xfb`) took the qcow2 branch; the 3-byte QCOW1 branch
  below it was dead code for real images, making the
  `docs/format-coverage.md` "QCOW1 detection: Yes" claim
  wrong. Observed effect: `instar info` on a fresh
  `qemu-img create -f qcow` image printed `file format: qcow2`,
  `virtual size: 0` and a garbage qcow2 format-specific block;
  `instar convert` failed with the misleading "input image has
  zero virtual size". A second latent hazard sat behind it:
  `chain::ImageFormat::from_str` already mapped `"qcow1"` past
  the issue-#444 gate with no reader arm, so fixing detection
  alone would have flipped qcow1 to silent raw reads — the
  phase-4 plan ordered the reader arm (commit `77f32ca`, step
  4b) strictly before the detection fix (commit `c421f75`,
  step 4c), closing the hazard window. Detection is now
  version-aware: `QFI\xfb` + version 1 => qcow1 (via the new
  `src/crates/qcow1/` reader), any other version keeps the
  qcow2 route. One latent divergence from qemu was found and
  recorded rather than fixed: a version-0 `QFI\xfb` image
  probes as raw under qemu but refuses under instar's qcow2
  route (see `docs/quirks.md` "Format-coverage phase 4").
* **FIXED (phase 4, commit `467d24a`): `INFO_RESULT_FLAG_ENCRYPTED`
  was never consumed.** Also found during phase-4 planning:
  qemu-img prints `encrypted: yes` (human, between disk size
  and cluster_size) and `"encrypted": true` (JSON) for
  encrypted images, but instar's emitters never consumed the
  flag, so the line was never printed. Latent at the time — no
  baseline in the tree contained the line (the bare-LUKS
  goldens matched qemu in omitting it) — with phase 4's
  AES-encrypted qcow1 fixture (`qcow1-encrypted`) becoming the
  first baseline to need it. Both host emitters now consume
  the flag, gated off for the `"luks"` format string so the
  LUKS goldens stay byte-identical (verified by a full
  `test_info_safe` run, zero regressions); encrypted qcow2
  images pick up the line for free as a side effect, with no
  baseline churn since no existing golden covers that case.
* **instar-testdata: parallels driver missing from the
  6.0.0–6.2.0 static qemu-img builds.** Found by step 4b's
  driver spot-check (2026-07-17): `-f parallels` fails with
  "Unknown driver" on all five 6.x binaries (compile-time
  absence — `build-qemu-img.sh`'s pre-8.0 branch never
  explicitly enables per-format drivers), while 7.0.0+ all
  have it and bochs/cloop/dmg are present in all 80. A stock
  qemu 6.x includes parallels, so the binaries misrepresent
  real qemu; the five are being rebuilt with the driver
  enabled (script fix + rebuild in progress), and parallels
  manifest/baseline work is deferred until they land.
* **instar-testdata: committed `profiles/` and
  `version-map.json` are stale relative to `raw/`.** Also
  found by step 4b: baseline generation runs after 23 June
  were never followed by `detect-profiles.py`, and a fresh
  recompute (excluding phase-1 images) produces a different
  profile structure than what is committed. Since
  `detect-profiles.py` rebuilds the whole profile tree,
  regeneration is deferred to a single reviewed catch-up
  change after the 6.x rebuild, covering the pre-existing
  drift, the three new format-coverage images, and
  parallels together.
* **instar-testdata: `detect-profiles.py` corrupts
  regenerated profiles.** Found by the phase-1 catch-up run
  (2026-07-17): the preserve-manually-maintained-baselines
  check compares mismatched id granularities (`f.stem` keeps
  `.stdout` on one side, `rsplit('.', 1)` strips it on the
  other), so it never matches, flags every image as
  manually maintained, and stamps one arbitrary stale
  old-profile snapshot into every new profile bucket —
  41/44 pre-existing images got wrong content while `raw/`
  stayed correct. Verified no real instar divergence hides
  behind it (live instar output byte-matches `raw/` for
  spot-checked images). Fix + rerun executed as part of
  phase 1; the mechanism must only preserve images with no
  `raw/` data (the hand-maintained `skip_qemu_img` set).

### Documentation index maintenance

When creating a new master plan from this template, update
the following files in `docs/plans/`:

* **`index.md`** — add a row to the *Master plans* table
  with the creation date, a link to the plan, a one-line
  intent summary, the initial status, and links to each
  phase plan file. Keep the table in chronological order.
* **`order.yml`** — add an entry for the new master plan
  so it appears in the documentation navigation bar. Phase
  files should *not* be added to `order.yml`.

When all phases of a plan are complete, update the status
column in `index.md` to *Complete*.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
