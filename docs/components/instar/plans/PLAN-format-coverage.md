# Format coverage expansion

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
   the 384KB guest binary cap. Proposal: v1 = zlib +
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
| 1. Detection + info parity (Parallels, Bochs, cloop, DMG; settle the trailer-probe question) | PLAN-format-coverage-phase-01-detection.md | Not written |
| 2. VDI convert-from (dynamic + static read path, new `src/crates/vdi/`) | PLAN-format-coverage-phase-02-vdi-read.md | Not written |
| 3. Parallels convert-from (v2 read path, new `src/crates/parallels/`) | PLAN-format-coverage-phase-03-parallels-read.md | Not written |
| 4. QCOW1 convert-from (read path, likely inside `src/crates/qcow2/` as a sibling parser) | PLAN-format-coverage-phase-04-qcow1-read.md | Not written |
| 5. DMG convert-from (koly trailer + BLKX chunk table + zlib chunks, new `src/crates/dmg/`) | PLAN-format-coverage-phase-05-dmg-read.md | Not written |
| 6. QED decision: read path or documented refusal (see Open question 1) | PLAN-format-coverage-phase-06-qed.md | Not written |
| 7. Docs: qemu-img-parity axis in format-coverage.md, README/ARCHITECTURE/CHANGELOG updates | PLAN-format-coverage-phase-07-docs.md | Not written |

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

* Guest binaries must stay under the 384KB per-operation
  cap (`make check-binary-sizes`); DMG's inflate reuse and
  any new decompressor need size budgeting up front.
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
(memory layout, the 384KB guest binary cap, the
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
      (384KB limit per operation).
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

* `make instar` builds and `make lint` is clean.
* Guest binaries pass `make check-binary-sizes` (384KB limit).
* All Rust unit tests pass (`make test-rust`).
* All Python integration tests pass (`make test-integration`).
* `pre-commit run --all-files` passes.
* `instar info` detects every on-disk image format a current
  qemu-img can probe (or the phase-1 plan records why a
  specific format's probe is not reproducible host-side).
* VDI, Parallels, QCOW1, and DMG images convert correctly to
  every existing output format, cross-validated against
  `qemu-img convert` output.
* Bochs, cloop, vvfat (and QED, per the phase-6 decision)
  produce clean, tested, documented refusals rather than
  misdetection as raw.
* New format parsing lives in shared crates under
  `src/crates/`, `no_std`-compatible for guest use, with
  coverage-guided fuzz targets.
* The staged instar-testdata images (parallels-v1,
  parallels-v2, empty.bochs, simple-pattern.cloop) are
  exercised by tests, and new fixtures exist for VDI-static
  and DMG.
* `docs/format-coverage.md` (or a sibling document) tracks
  format coverage against qemu-img's real format-driver
  roster, not just oslo.utils.
* `ARCHITECTURE.md`, `README.md`, `AGENTS.md`, and
  `CHANGELOG.md` have been updated as needed.

### Future work

We should list obvious extensions, known issues, unrelated bugs
we encountered, and anything else we should one day do but have
chosen to defer to here so that we don't forget them.

* DMG bzip2 (UDBZ) and lzfse (ULFO) chunk codecs (deferred
  from phase 5 per Open question 3).
* Write/create/output support for VDI or Parallels, if real
  demand appears.
* VMDK subformat expansion (twoGbMaxExtentSparse output,
  ESX variants) — separate plan.
* `map` / `measure` / `dd` support for the new input
  formats, where each phase plan chose to defer it.

### Bugs fixed during this work

This section should list any bugs we encounter during
development that we fixed. You should also scan the relevant
github bug tracker to see if there are any directly related
bugs that we should either resolve as part of this master
plan, or at least be aware of when planning. (A scan on
2026-07-17 found no existing format-coverage issues; the
open issues are fuzz crashes, consistency checks, and qcow2
operation bugs.)

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
