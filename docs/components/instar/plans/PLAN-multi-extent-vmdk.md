# Multi-extent VMDK output: twoGbMaxExtentSparse and twoGbMaxExtentFlat

## Status: In progress

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

Consult `ARCHITECTURE.md` for the overall system structure
(host VMM, KVM guest, call table, device emulation).
Consult `AGENTS.md` for build commands, project conventions,
code organisation, and the security model summary. Consult
`docs/` for format-specific documentation (`docs/qcow2/`,
`docs/raw/`, etc.) and `docs/commentary/` for architectural
decisions and design rationale.

<!-- shared-block: plan-file-conventions v2 -->
Plan file conventions (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-file-conventions.md`):

- All planning documents live in `docs/plans/`.
- Detailed planning gets one plan file per phase. Phase files are
  named for their master plan, sit in the same directory as it,
  and append `-phase-NN-descriptive` before the `.md` extension.
- The master plan tracks its phases in a table under its Execution
  section. `Merged` is last, and the push audit is the last row,
  for the reasons given in `plan-push-audit-phase`:

  | Phase | Plan | Status | Merged |
  |-------|------|--------|--------|
  | 1. Schema migration | PLAN-thing-phase-01-schema.md | Not started | |
  | 2. Public API | PLAN-thing-phase-02-api.md | Not started | |
  | 3. Push audit | - | Not started | |

- One commit per logical change, and at minimum one commit per
  phase. Unrelated changes are not batched into a single commit.
  Each commit is self-contained: it builds, passes tests, and has
  a message explaining what changed and why.
<!-- shared-block-end -->

## Situation

The 2026 technical goal *VHD/VMDK writers done* has one leaf left.
`PLAN-differencing.md` closed the differencing leaves;
`PLAN-bat-population.md` closed VHDX fixed output, vmdk/vpc/vhdx
preallocation (as qemu parity, a no-op) and the per-format BAT
population pattern. What remains is VMDK `twoGbMaxExtent*` output and
the prerequisite the goals graph calls the *multi-output-device call
table*. Closing both turns the whole waypoint green.

Three earlier plans deferred to that prerequisite by name and none of
them built it: `PLAN-create-phase-05-backing-file.md` ("Future work;
requires multi-output-device call-table support"), `PLAN-resize.md`
(multi-file resize, twice), and `PLAN-bat-population.md`, which put it
out of scope because nothing in that plan needed it -- monolithicFlat
create is host-only and the convert path that writes it already uses
one output device.

### What was measured

Everything in this section was measured on 2026-11-16 against
`24b629cb` (the merge of #912), with the host's qemu-img 10.0.13
(Debian `1:10.0.13+ds-0+deb13u1`). The instar binary used was
`src/target/release/instar` dated 2026-10-19, which predates several
merges; every instar result below is a *host-side* refusal or a
host-side descriptor walk, reached before any guest operation starts,
so the stale binary does not affect them. Phase 1 re-measures against
a freshly built binary anyway.

qemu-img creates both subformats:

```
$ qemu-img create -f vmdk -o subformat=twoGbMaxExtentSparse sp.vmdk 5G
$ ls
sp.vmdk (417 bytes)  sp-s001.vmdk  sp-s002.vmdk  sp-s003.vmdk
$ qemu-img create -f vmdk -o subformat=twoGbMaxExtentFlat fl.vmdk 5G
$ ls
fl.vmdk (415 bytes)  fl-f001.vmdk  fl-f002.vmdk  fl-f003.vmdk
```

So there is a real oracle for both. This is the question that
collapsed the preallocation leaf in `PLAN-bat-population.md` into a
parity no-op, and here it answers the other way.

Extent naming is `<stem>-s%03d.vmdk` for sparse and `<stem>-f%03d.vmdk`
for flat. Flat extents are the full 2 GiB in length but sparse on disk
(the 5G flat image occupies 4 KiB). The descriptor qemu wrote:

```
# Disk DescriptorFile
version=1
CID=4de986e1
parentCID=ffffffff
createType="twoGbMaxExtentSparse"

# Extent description
RW 4194304 SPARSE "sp-s001.vmdk"
RW 4194304 SPARSE "sp-s002.vmdk"
RW 2097152 SPARSE "sp-s003.vmdk"

# The Disk Data Base
#DDB

ddb.virtualHWVersion = "4"
ddb.geometry.cylinders = "10402"
ddb.geometry.heads = "16"
ddb.geometry.sectors = "63"
ddb.adapterType = "ide"
ddb.toolsVersion = "2147483647"
```

Note the `ddb.*` block. `docs/create.md`'s Future work records that
instar emits none of those lines; open question 3 takes that up.

The 80-build sweep (qemu 6.0.0 to 10.2.0) confirmed the naming and
descriptor shape above for both subformats; the only difference
across builds is `ddb.toolsVersion = "2147483647"` appearing from
6.2.0 onward. The pin is `tests/golden/qemu-create-pins.json`.

**The ceiling.** A 40 GiB image is 20 extents, which qemu-img reads
without complaint. instar refuses it:

```
$ instar convert -O qcow2 big-flat.vmdk out.qcow2
Error: "chain depth 21 plus output device exceeds maximum of 16 devices"
$ instar convert -O qcow2 fl.vmdk out.qcow2        # 3 extents
(succeeds)
```

So instar cannot read any `twoGbMaxExtent*` image past 15 extents --
about 30 GiB -- today. That is a shipping limitation, not a
hypothetical, and it bounds any writer built on the same device model.

Two other current refusals, both clean and both consistent with the
docs: sparse extents are refused at descriptor resolution ("extent 0
has kind Sparse; only FLAT extents are supported"), and `instar map`
refuses every flat source ("monolithicFlat source images are not yet
supported"). `instar info` walks a 20-extent flat descriptor fine and
lists `/extents.0` through `/extents.19`, because `info` resolves the
descriptor on the host and never opens the extents as devices.

### What the code does today

Source citations are to `24b629cb`. Each phase re-reads the lines it
depends on, because unrelated merges move them.

The device model is already N-device capable at the virtio layer.
`DeviceSet` (`src/vmm/src/main.rs:717`) holds a `Vec<ManagedDevice>`,
each with an `is_input` flag (`:690-697`), assigns sequential MMIO
bases, and dispatches MMIO by address (`:772-790`). What is capped is
the total: `MAX_CHAIN_DEPTH = 16` (`:412`), and `add_device` *panics*
past it (`:736-750`). Operations guard with an explicit error first --
`:15088` is the convert guard that produced the message above -- so the
panic is not reachable through the CLI today. That guard is the
ceiling, and it counts inputs and the output together.

The call table is asymmetric. Input devices are indexed:

```rust
pub get_input_device_count: unsafe extern "C" fn() -> u32,
pub read_input_sector:  unsafe extern "C" fn(u32, u64, *mut u8, usize) -> bool,
pub get_input_capacity: unsafe extern "C" fn(u32) -> u64,
pub get_input_sector_size: unsafe extern "C" fn(u32) -> usize,
```

(`src/shared/src/lib.rs:1060-1078`.) Output devices are not:

```rust
// Output device functions (single device)
pub write_output_sector: unsafe extern "C" fn(u64, *const u8, usize) -> bool,
pub get_output_capacity: unsafe extern "C" fn() -> u64,
pub get_output_sector_size: unsafe extern "C" fn() -> usize,
```

(`:1083-1092`), with `read_output_sector` (`:1227-1234`) appended later
for resize. The table is append-only by convention: several entries
carry the comment "Appended at the end of `CallTable` so existing
operation binaries do not need to recompile against shifted offsets to
keep working". Anything this plan adds goes at the end.

In the guest core, inputs are an array and the output is a single
cell: `INPUT_DEVICES: [Option<VirtioBlock>; MAX_INPUT_DEVICES]`
(`src/core/src/main.rs:129-130`, `MAX_INPUT_DEVICES = 16` at `:49`)
against `OUTPUT_DEVICE: SingleThreadCell<Option<VirtioBlock>>`
(`:135`). Device init (`:176-233`) fills inputs from index 0, then
places the output at index `active_count` -- so the output's device
index already varies with chain depth -- and bounds-checks it against
`MAX_INPUT_DEVICES` (`:211-216`).

The host tells the guest about devices over serial, **by name**:
`read_config` (`src/core/src/serial.rs:130-170`) matches `"input"`,
then `"input1"` through `"input15"` via `parse_device_index`, then
`"output"`. Extending that to `"output1".."outputN"` needs no protobuf
schema change, only new device names and a wider `DeviceConfig`
(`:72-95`).

**The `.bss` hazard.** `OUTPUT_DEVICE` and `INPUT_DEVICES` are exactly
the statics that have twice forced the guest memory map to move.
`OPERATION_LOAD_ADDR` (`src/shared/src/lib.rs:632`) documents it: core's
`.bss` once overflowed 0x20000 and device init wrote a `VirtioBlock`
over the loaded operation's code at 0x20380-0x203c7, corrupting amend's
header cross-check; the flat-binary size check missed it because the
flat image excludes `.bss`. The address was raised to 0x22000, then to
0x30000, and `scripts/check-binary-sizes.sh` now measures the
`.bss`-inclusive ELF extent with an 85% warning threshold. Measured
today, core sits at 68 KiB of its 128 KiB budget (53%). Turning
`OUTPUT_DEVICE` into a 16-slot array would add roughly a second copy of
the `INPUT_DEVICES` region to that figure. Open question 1 turns on
this as much as on the ceiling.

Finally, the surfaces that refuse these subformats today, all of which
this plan changes or deliberately leaves: `create` rejects
`subformat=twoGbMaxExtent*` (`docs/create.md:144`), `resize` rejects
every multi-extent subformat (`docs/resize.md:263-264`),
`format-internals.md:89` records sparse multi-extent as "detected and
rejected gracefully", and `docs/check.md:58` claims check validates
multi-extent twoGbMaxExtentFlat while issue #822 says check refuses
monolithicFlat outright.

## Mission and problem statement

Give instar a device model that can write -- and read -- a VMDK split
across an arbitrary number of extent files, and use it to emit
`twoGbMaxExtentSparse` and `twoGbMaxExtentFlat` the way qemu-img does,
at any virtual size qemu-img supports.

The goals graph calls the prerequisite "multi-output-device call
table", and the measurement above is the reason this plan does not
simply build that. Extents are capped at 2 GiB each and the device
model is capped at 16 slots, so *simultaneous* output devices reach
about 30 GiB and stop. qemu-img does not stop there. A design that
opens every extent at once moves instar's ceiling by one device and
leaves a parity gap that grows with image size; it would also roughly
double the virtio statics in core's `.bss`, the one region of the guest
memory map with a history of silently corrupting loaded operations.
Open question 1, resolved on 2026-11-16, takes sequential extent
rotation instead, which bounds the device count at one regardless of
extent count and fixes the read-side ceiling with the same mechanism.

In scope:

* A device-rotation mechanism in the call table, core and VMM, letting
  a guest operation write (and read) a sequence of extent files
  through a bounded number of device slots.
* Lifting the read-side ceiling: `twoGbMaxExtent*` sources with more
  than 15 extents, and `twoGbMaxExtentSparse` sources at all.
* `instar create -f vmdk -o subformat=twoGbMaxExtentFlat` (and
  `--subformat`), host-only, matching qemu's extent naming, descriptor
  and per-extent sizing.
* `instar create -f vmdk -o subformat=twoGbMaxExtentSparse`, through
  the guest, with a streaming per-extent grain directory and grain
  tables in the same shape `PLAN-bat-population.md` established for the
  VHDX BAT: compute each entry from its index, stream through one
  bounded buffer, never hold the table in memory.
* `instar convert -O vmdk --subformat twoGbMaxExtentSparse|Flat`.
* `instar measure -O vmdk -o subformat=twoGbMaxExtent*`.
* A multi-extent descriptor emitter in the crates (`no_std`,
  panic-free), shared by create and convert.
* `check` over instar's own multi-extent output, and a decision on
  what `resize` does with one.
* Extent path derivation and its safety: N files written from one CLI
  argument is a new blast radius for a tool whose threat model assumes
  hostile images.
* A structural multi-extent comparator for the tests and the
  differential fuzzer, since `qemu-img info` alone cannot tell a
  correct extent split from a wrong one.

Out of scope:

* `twoGbMaxExtent*` as a *backing file* or in a backing chain. The
  chain machinery addresses images by device slot, and rotation
  changes what a slot means mid-operation; combining the two wants its
  own plan.
* Real preallocation of the new subformats, for the reasons
  `PLAN-bat-population.md` open question 1 gives: qemu-img leaves them
  sparse, so allocating would be an unflagged instar-only behaviour
  with no oracle. `falloc` and `full` stay accepted no-ops and
  `metadata` stays refused.
* Retrofitting `ddb.*` lines onto the existing VMDK subformats. Open
  question 3 keeps it out and says why.
* `instar map` on flat sources. It refuses every flat source today,
  not just multi-extent ones, and that refusal is older and wider than
  this plan.
* Fixing #822 (check refuses monolithicFlat while `docs/check.md` says
  it validates it). This plan's check phase must not *widen* that
  inconsistency, but closing it belongs to the issue.

## Open questions

Each question carries the planner's recommendation and its reasoning.
The operator resolved questions 1 and 2 on 2026-11-16, before phase 1's
plan was written, adopting both recommendations as drafted; each
question records it. Questions 3 to 6 are still open, and phase 1's
measurements settle much of 4 and 5.

1. **Simultaneous output devices, or sequential extent rotation?**
   *Recommendation: rotation.* Four reasons, in order of weight.

   *Parity.* Measured above: 16 slots total, 2 GiB per extent, so
   simultaneous devices support 15 extents against qemu's 20-and-up.
   Rotation bounds the device count at one and has no size ceiling of
   its own.

   *The access pattern already suits it.* Conversion and creation walk
   the virtual disk in ascending order, and extent *k* owns exactly
   `[k*2GiB, (k+1)*2GiB)`. Two data extents are never live at once. A
   sparse extent's grain directory and grain tables live inside that
   same extent, and the streaming emitter from
   `PLAN-bat-population.md` finalises them without a second pass.

   *It fixes the read side.* The shipping bug is the same ceiling. One
   mechanism, rotated over inputs as well as outputs, closes both; an
   indexed-output design closes neither.

   *Core `.bss`.* Rotation keeps `OUTPUT_DEVICE` a single cell. A
   16-slot output array adds roughly another `INPUT_DEVICES` to the
   region that has twice forced `OPERATION_LOAD_ADDR` to move and once
   silently corrupted a loaded operation.

   The cost is honest and should be weighed: rotation adds control flow
   to a boundary that currently has none. A new call-table entry
   ("finish the current output extent and bind the next") means the
   host opens, closes and `fsync`s files mid-operation, and a failure
   between extents leaves a partially written image the guest cannot
   see. Phase 8 owns that lifecycle and its cleanup.

   *Alternative, if the operator prefers the simpler change:* indexed
   output devices capped at the slot budget, with sizes above the cap
   refused and the divergence documented. That is less code and no new
   control flow, and it would still close the goals-graph node as
   drawn, but instar would carry a size limit qemu does not.

   **Resolved 2026-11-16: rotation**, as recommended. The alternative
   stays recorded here so a later reader knows it was weighed and why
   it lost, not to leave the decision open. Phase 8 owns the lifecycle
   cost this choice accepts.

   **Amended 2026-11-19, for the read path only.** Phase 3's survey
   found that this architecture has no guest-to-host request channel:
   the host writes `CHAIN_CONFIG_ADDR` before the guest starts and the
   guest does I/O through virtio, so rotation would have to invent a
   channel for the guest to say "bind extent k" and block until it was
   done. Reads do not need one, because a virtio request already
   carries the offset the guest wants: the host concatenates a
   descriptor's extents behind one device and publishes an extent table
   in `ChainConfig` so the guest can still parse each sparse extent's
   own header. Phase 3 therefore does not rehearse a rotation
   mechanism, which was one of the reasons given above for doing the
   read side first. Whether the write path can also route host-side
   instead of rotating is left open for phases 6 to 8; this amendment
   does not decide it.

2. **Does lifting the read-side ceiling belong in this plan?**
   *Recommendation: yes.* It is the same mechanism and the same
   ceiling, it is a bug users can hit today on images instar otherwise
   supports, and the differential fuzzer cannot check what this plan
   writes unless instar can read a 20-extent image back. Doing it first
   (phase 3) also exercises rotation on the read path, where a mistake
   corrupts nothing, before the write path trusts it.

   *Against:* it widens a plan that is already large, and the read fix
   has value on its own. If the operator wants it split, phase 3 is the
   clean seam -- it would become its own small plan that this one
   depends on.

   **Resolved 2026-11-16: in this plan, as phase 3**, as recommended.

3. **Does multi-extent output emit qemu's `ddb.*` lines?**
   *Recommendation: yes for the new subformats, and do not retrofit the
   existing ones here.* The descriptor is small, text, and compared
   byte-for-byte against qemu's in the pin tests; omitting six lines
   qemu always writes would mean either a weaker oracle or a documented
   divergence on brand-new output, and the geometry values are
   computable from the virtual size. Retrofitting `monolithicSparse`,
   `streamOptimized` and `monolithicFlat` would change the bytes of
   every VMDK instar has ever written, churn every VMDK baseline, and
   deserves its own differential-fuzz exposure. Phase 4 files the
   retrofit as an issue and names it in Future work.

   *The cost of the recommendation:* instar's VMDK descriptors become
   inconsistent with each other until that retrofit lands -- new
   subformats carry `ddb.*`, old ones do not. Phase 17 documents it
   explicitly rather than leaving a reader to discover it.

4. **How are extent paths derived, and what is refused?**
   *Recommendation:* derive strictly from the output argument's own
   stem and directory (`out.vmdk` -> `out-s001.vmdk`), never from any
   string in an input descriptor; refuse if any target already exists;
   refuse a stem that would collide with the descriptor itself; and
   write every extent before the descriptor, so a failure never leaves
   a descriptor pointing at files that do not exist. Phase 1 must
   measure what qemu-img actually does on each of those -- particularly
   pre-existing targets -- because matching it matters more than any
   rule invented here.

   *Measurement (phase 1, 80 qemu builds; not a resolution):* a
   pre-existing file where an extent goes is not refused, in either
   split subformat. On all 80 builds qemu returns rc 0 with empty
   stderr and writes the image exactly as it does without the
   obstruction. A second hard link kept to the file reports the
   extent's length, not the original 1 MiB, so qemu truncated it in
   place rather than unlinking it and creating its own file --
   shrinking it for a sparse extent and growing it for a flat one,
   which is the same `ftruncate` in both directions. Any other hard link, and any open
   descriptor, follows the image. What qemu does is not by itself
   what instar should do: silently truncating a file the user did not
   name is a different risk calculus for a tool whose threat model
   assumes hostile input. The recommendation above stands
   unresolved; the decision is the operator's.

5. **What is the maximum extent count, and what happens past it?**
   The `%03d` naming implies 999 extents, about 2 TiB. *Recommendation:*
   measure qemu's real ceiling in phase 1 and match it, refusing above
   it with a message that names the limit and the size that would reach
   it, before the output file is opened -- the shape
   `PLAN-bat-population.md` used for VHDX's 64 TiB refusal, which
   leaves nothing behind where qemu-img leaves an empty file.

   *Measurement (phase 1; not a resolution):* the premise above is
   false. `%03d` is a minimum field width, not a cap. Probed on
   6.0.0, 7.2.0, 8.2.0 and 10.2.0, both split subformats: at 2000 GiB
   all four builds write 1000 extents, rc 0, empty stderr, and the
   thousandth descriptor line is verbatim
   `RW 4194304 SPARSE "image-s1000.vmdk"`. At 32 TiB all four write
   16384 extents. At 1 PiB 10.2.0 still does not refuse; a 60 s
   timeout stopped it at extent 70168 of 524288. The only refusal
   anywhere near is qemu-img's generic size parser, identical on
   every build: `qemu-img: Invalid image size specified. Must be
   between 0 and 9223372036854775807.` So there is no qemu ceiling
   to match. The recommendation above was written before this
   measurement and its "measure and match" reasoning no longer
   applies as stated. Whether instar matches qemu or imposes a
   documented limit of its own is the operator's decision, still
   open.

6. **Is flat create host-only, like monolithicFlat?**
   *Recommendation: yes,* and it should land *before* the ABI work.
   A flat multi-extent image is N sparse files of known length plus a
   text descriptor -- no format emission, nothing the guest is needed
   for. `PLAN-bat-population.md` already established host-only
   monolithicFlat create. That makes phase 5 a cheap, user-visible
   delivery that does not depend on phases 6 to 8, and it de-risks the
   descriptor emitter and the path rules by exercising them first.

## Execution

<!-- shared-block: plan-push-audit-phase v4 -->
Push audit phase (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-push-audit-phase.md`):

- Every master plan ends with a phase that runs the repository's
  `PUSH-AUDIT.md` over the whole plan's work. It is the last row of
  the Execution table and it is not optional. The rule binds every
  plan that carries the phase, which is decidable from the plan file
  alone: a plan that is already `Complete`, `Abandoned` or
  `Superseded` and does not carry the phase is not reopened to
  acquire one, and a plan that has the phase runs it even if it
  reaches `Complete` before the phase does.
- That phase audits the accumulated diff of every phase in the plan
  against the default branch, not the diff of the last phase alone.
  Auditing one phase at a time would miss what the phases did to
  each other -- the duplicated helper that only exists once phases
  three and six have both landed, the doc page that phase two made
  wrong and phase five never revisited.
- Once the plan's phases have merged, a diff against the default
  branch is empty and would read as a clean audit. The range is not
  reliably derivable after the fact either: unrelated work lands on
  the default branch between phases, so anything anchored on "since
  the plan file appeared" is far too wide. It has to be recorded. As
  each phase lands, what put it on the default branch goes into the
  plan: the merge commit of its pull request, whose diff against its
  first parent is the whole of what landed, or -- where the phase
  landed directly -- every commit of the phase, or its `first..last`
  range. A single commit is only ever enough when it is a merge
  commit.
- Where the Execution phases are a table, that record is a `Merged`
  column, added last so that a row which omits it still reaches
  `Status`; where they are prose sections it is a `Merged:` line in
  the phase's own section. The `Status` column keeps its single
  vocabulary term and nothing else (see `plan-status-vocabulary`).
  A phase that landed in another repository records `<repo> <sha>
  (#pr)` and is audited against that repository's default branch, as
  part of the pull request that lands it; the plan's own push-audit
  phase cites that audit rather than re-running it.
- Phases that landed before the plan started recording them are
  reconstructed rather than left blank. Recover what you can from
  `gh pr list --state merged` and `git rev-list --first-parent`, and
  say in the plan that the range was reconstructed. Do not trust a
  path-filtered `git log` on its own: it lists the commits that
  touched a path without saying which arrived directly and which
  arrived inside a pull request, and recording a commit that came in
  under a merge audits one commit of that pull request rather than
  the pull request. A reconstructed record may be a summary table in
  the audit phase's own section rather than a column or a line in
  the Execution table, which keeps retrospective archaeology out of
  a table that tracks live status. Where a phase accreted over
  months of unrelated commits and no range is recoverable, say that
  instead and name the paths the audit read -- an audit that says
  what it could not scope is a result; one that silently audits
  nothing is not.
- Findings land as their own pull request against the default
  branch, and the plan is not complete until they are resolved or
  explicitly declined in writing. A finding that is declined says
  why, in the plan, where the next reader will find it.
- Where the audit finds nothing, record that in the plan in one
  sentence. It is a real result, and a run of them is the evidence
  for making the phase conditional rather than mandatory.
- A repository with no `PUSH-AUDIT.md` still carries the phase, and
  the phase says that the runbook does not exist yet and what was
  done instead. Silently omitting it is what let the audit go
  untriggered for as long as it did. What is done instead is at
  least the checklist below, applied to the recorded ranges: it is
  a floor, not a substitute for a runbook, and a plan adds the
  questions its own repository's blast radius raises. Each item
  gets a verdict, or a sentence saying why it does not apply.
  Do not borrow another repository's runbook instead: a runbook is
  written for one repository's blast radius.
  1. Every recorded landing takes one of the forms above: a merge
     commit with two parents, whose diff is read against its first
     parent; every commit of a direct landing, or its `first..last`
     range; a landing in another repository, whose own audit is
     cited; or a statement that no range is recoverable, naming the
     paths the audit read.
  2. Anything the diff added that runs unattended -- cron, a timer,
     a scheduled workflow, a self-update -- reports its own failure
     somewhere a human sees, or is named as not doing so.
  3. No secret or token entered any repository, and no private
     hostname or IP address entered a repository more public than
     the one it came from.
  4. Anything the diff removed or renamed has no consumer left,
     checked by grep across every repository that could consume it
     rather than by assumption, with the greps stated.
  5. Where the repository publishes something -- an image, a
     release, a package -- a broken change fails closed: one that
     publishes nothing is recoverable, one that publishes something
     wrong is not.
<!-- shared-block-end -->

The phase order is chosen so that the cheap, user-visible delivery
(phase 5, host-only flat create) lands before the ABI change, and so
that rotation is proved on the read path (phase 3), where a mistake
cannot corrupt an output, before the write path depends on it.

| Phase | Plan | Status | Merged |
|-------|------|--------|--------|
| 1. Pin and measure qemu's output | [PLAN-multi-extent-vmdk-phase-01-pin.md](/components/instar/plans/PLAN-multi-extent-vmdk-phase-01-pin/) | Complete | 34832d47 |
| 2. Fixtures and baselines | [PLAN-multi-extent-vmdk-phase-02-fixtures.md](/components/instar/plans/PLAN-multi-extent-vmdk-phase-02-fixtures/) | Complete | 2e8a3022 |
| 3. Read side: lift the extent ceiling | [PLAN-multi-extent-vmdk-phase-03-read-ceiling.md](/components/instar/plans/PLAN-multi-extent-vmdk-phase-03-read-ceiling/) | In progress | |
| 4. Multi-extent descriptor emitter | PLAN-multi-extent-vmdk-phase-04-descriptor.md | Not started | |
| 5. Host-only flat create | PLAN-multi-extent-vmdk-phase-05-flat-create.md | Not started | |
| 6. ABI: output rotation | PLAN-multi-extent-vmdk-phase-06-abi.md | Not started | |
| 7. Core: rotation in the guest | PLAN-multi-extent-vmdk-phase-07-core.md | Not started | |
| 8. VMM: extent lifecycle and safety | PLAN-multi-extent-vmdk-phase-08-vmm.md | Not started | |
| 9. Streaming grain tables | PLAN-multi-extent-vmdk-phase-09-grain-tables.md | Not started | |
| 10. Sparse create | PLAN-multi-extent-vmdk-phase-10-sparse-create.md | Not started | |
| 11. convert -O vmdk multi-extent | PLAN-multi-extent-vmdk-phase-11-convert.md | Not started | |
| 12. measure | PLAN-multi-extent-vmdk-phase-12-measure.md | Not started | |
| 13. check and resize | PLAN-multi-extent-vmdk-phase-13-check-resize.md | Not started | |
| 14. Baselines across the version matrix | PLAN-multi-extent-vmdk-phase-14-baselines.md | Not started | |
| 15. Integration tests | PLAN-multi-extent-vmdk-phase-15-integration.md | Not started | |
| 16. Fuzzing | PLAN-multi-extent-vmdk-phase-16-fuzz.md | Not started | |
| 17. Documentation | PLAN-multi-extent-vmdk-phase-17-docs.md | Not started | |
| 18. Push audit | - | Not started | |

### Phase intents

Each phase gets its own plan file before it is implemented; these are
the one-paragraph intents the phase plans expand.

**1. Pin and measure qemu's output.** Establish the oracle before
writing anything. Phase 1's survey corrected this section: the
cross-version sweep already exists, built by
`PLAN-bat-population-phase-01-pin.md` -- `tools/sweep-qemu-create.py`,
`tests/helpers/qemu_pins.py`, `tests/golden/qemu-create-pins.json` and
`tests/test_qemu_create_pins.py`, over the 80 static builds in
`instar-testdata/qemu-img-binaries/x86_64/<version>/qemu-img` -- and
this phase extends it rather than starting one. It also adds the VMDK
structural parser that phases 10, 11 and 16 depend on, and makes the
pinned descriptor able to prove an extent naming scheme and ordering,
which it cannot today. Measure, per version: descriptor bytes for both
subformats; extent naming; per-extent sector counts for sizes that are
and are not multiples of 2 GiB; the `ddb.*` values and how geometry is
derived; the maximum extent count and the refusal past it; behaviour
when a target extent already exists; and whether any of this moved
between versions. Record every answer as a measurement with the version
that produced it. **Nothing in later phases may assert a qemu-img
behaviour this phase did not measure** -- sub-agents have asserted
plausible-but-wrong qemu-img capabilities repeatedly in this
repository, and the preallocation leaf in `PLAN-bat-population.md`
turned on exactly this kind of check.

**2. Fixtures and baselines.** Add `twoGbMaxExtentSparse` fixtures to
`instar-testdata` with their manifest entries, and a test-time
generator for `twoGbMaxExtentFlat`. Phase 2's survey corrected this
section in three ways. An image past the current 15-extent ceiling
already exists -- `osboxes-vmdk-split-sparse`, a real VMware
Workstation image of 26 extents with full baselines -- but its extents
are 8304640 sectors (3.96 GiB) each under a
`createType="twoGbMaxExtentSparse"` descriptor, so it covers the
device-slot ceiling and not qemu's geometry, and it is kept as a
real-world foil rather than replaced. A split-flat fixture cannot be
stored at all: its extents are full-length sparse files and git-LFS
stores holes as bytes, measured at 22,548,578,304 bytes apparent for
the 11-extent case against 3,473,408 for the sparse one, which is why
`scripts/create-vmdk-flat-testdata.sh` already creates a split-flat
image and deletes its extents. And the pin's structural oracle is
sparse-only by design, so the `vmdk-multi-flat-*` cases record a null
structure and that is not a gap. Remember that repository takes direct
commits to `main`, never a merge request, and that it is git-LFS
backed. Generate baselines with `--no-commit` and validate the derived
profile directories rather than trusting them; they have twice been
silently wrong while the raw baselines were fine. A fixture that
carries `skip_qemu_img` gets no baselines at all, which is how the
existing multi-extent fixtures came to have none. The phase also fixes
the `info` divergences on multi-file VMDK that the operator pulled
forward from phase 3: the two its survey found, and a third the
80-build `children[]` sweep turned up while the phase ran.

**3. Read side: lift the extent ceiling.** Implement rotation on the
input path and remove the 15-extent limit for multi-extent sources, and
add `twoGbMaxExtentSparse` as a readable input. Read each extent's
declared sector count rather than inferring 2 GiB from the create type:
phase 2 measured the one real-world split-sparse image in the corpus
declaring 3.96 GiB extents under `twoGbMaxExtentSparse`. The `info`
divergences on multi-file VMDK -- the top-level `actual-size` reporting
the descriptor's allocation where qemu reports the sum over the
descriptor and every extent, `children[]` coming back in the opposite
order, and the version gate the array bypassed -- were first deferred
to this phase and then pulled back into phase 2 by the operator,
together with the
single-file substitution in `tests/helpers/comparators.py` that masked
the first of them. So this phase inherits a VMM that sums extent
allocations on the non-chain `info` paths only: phase 3's survey found
the chain walk still reporting the descriptor's own apparent length at
`src/vmm/src/main.rs:3809`, filed as
[#927](https://github.com/shakenfist/instar/issues/927) and pulled into
phase 3. That survey also replaced this paragraph's mechanism: the read
path concatenates a descriptor's extents into one device host-side and
hands the guest an extent table, rather than rotating a device slot,
because there is no guest-to-host request channel for a rebind to use
(open question 1 records the change). A mistake here fails a read, not
an image. Includes the four over-deep-chain guards at
`src/vmm/src/main.rs:6719`, `:13573`, `:14405` and `:15462` -- the
`:15088` this section used to cite is now sector-size validation -- and
`DeviceSet::add_device`'s panic, which should become unreachable by
construction rather than by a caller remembering to check.

**4. Multi-extent descriptor emitter.** One `no_std`, panic-free
emitter in the crates that writes a multi-extent descriptor from a
geometry, used by both create and convert, carrying the `ddb.*`
decision from open question 3. Files the retrofit issue for the
existing subformats.

**5. Host-only flat create.** `create -f vmdk -o
subformat=twoGbMaxExtentFlat`, written entirely on the host: N sparse
extent files of the measured lengths plus the descriptor. No ABI
change, no guest involvement. Carries the path-derivation rules from
open question 4 and the extent-count refusal from question 5.

**6. ABI: output rotation.** The call-table change, appended at the end
of `CallTable` for the back-compat reason the existing entries
document, plus the serial `DeviceConfig` extension. Re-measure core's
`.bss`-inclusive extent with `scripts/check-binary-sizes.sh` as part of
the phase's definition of done, not afterwards.

**7. Core: rotation in the guest.** The guest side of rotation in
`src/core/src/main.rs` and `serial.rs`: binding, rebinding, bounds, and
what an operation sees when rotation fails. Guest operations are
`no_std` and panic-free; a failed rotation is an error code, never a
fault.

**8. VMM: extent lifecycle and safety.** The host side: opening,
`fsync`ing and closing extents as the guest advances, the path rules,
refusing pre-existing targets, and cleanup when an operation fails
part-way so a failure never leaves a descriptor pointing at a
half-written set. This phase owns the blast radius that open question 1
names as rotation's cost.

**9. Streaming grain tables.** The per-extent grain directory and grain
table emitter, in the shape `PLAN-bat-population.md` established for
the VHDX BAT: computed per index, streamed through one bounded buffer,
never held in memory. The cluster-size and geometry matrix matters
here -- a fixed staging buffer that only ever saw one geometry is
exactly the defect the bitmap plan's phase 7 found.

**10. Sparse create.** `create -f vmdk -o
subformat=twoGbMaxExtentSparse` through the guest, on phases 6 to 9.

**11. convert -O vmdk multi-extent.** Both subformats as convert
targets.

**12. measure.** `measure -O vmdk -o subformat=twoGbMaxExtent*`,
against the phase 1 baselines. Note that 81 safe fixtures have no
measure baselines at any version (#845); new fixtures must not quietly
join them.

**13. check and resize.** `check` over instar's own multi-extent
output, and a decision on `resize`: today it refuses every multi-extent
subformat, and the honest options are to keep refusing with a better
message or to support growing by appending extents. Must not widen
#822's docs/behaviour mismatch.

**14. Baselines across the version matrix.** Generate and validate
baselines for every new case across the pinned qemu versions, and check
the distro matrix: RHEL's `qemu-kvm` omits several drivers, so a
capability is not a version.

**15. Integration tests.** Python integration coverage for every new
surface, including the structural comparator. Watch for the subunit
attachment trap: an attachment over 4 MiB kills a stestr worker and the
run still exits 0, so check "Ran: N" rather than trusting a green exit.

**16. Fuzzing.** Coverage targets for the emitter and the descriptor
parser, and differential-fuzz arms for both subformats with a
structural comparator -- `qemu-img info` cannot tell a correct extent
split from a wrong one, the same reason phase 11 of
`PLAN-bat-population.md` needed a VHDX structural comparator. Make sure
the new options are actually drawn: an option the picker never draws is
not coverage.

**17. Documentation.** `docs/create.md`, `docs/convert.md`,
`docs/measure.md`, `docs/resize.md`, `docs/check.md`,
`docs/format-internals.md`, `docs/vmdk/vmdk-format.md`, the CHANGELOG,
and `ARCHITECTURE.md` -- the last only because rotation changes the
shape of the device model, which is exactly what that file is for.
Documents the `ddb.*` inconsistency from open question 3 explicitly.

**18. Push audit.** Per the shared block above.

## Agent guidance

### Execution model

<!-- shared-block: subagent-execution-model v1 -->
Sub-agent execution model (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/subagent-execution-model.md`):

All implementation work is done by sub-agents, never in the
management session. The management session is reserved for
planning, review, and decision-making. This keeps the management
context lean and avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with the
   brief from the plan, at the recommended effort level and model.
3. **Review** the sub-agent's output in the management session.
   Check the actual files -- the sub-agent's summary describes
   what it intended, not necessarily what it did.
4. **Fix or retry** if the output is wrong. Diagnose whether the
   brief was insufficient (improve it) or the model was too light
   (upgrade it), then re-run.
5. **Commit** once the management session is satisfied.

This applies to all steps, including high-effort ones. If a
sub-agent cannot succeed even with a detailed brief and the right
model, that is a signal the brief needs improving, not that the
management session should do the implementation itself.

Use `isolation: "worktree"` for sub-agents when the change is
risky or experimental; the worktree is discarded if the output is
unsatisfactory. For safe, well-understood changes, sub-agents can
work directly in the main tree.
<!-- shared-block-end -->

### Planning effort

The ABI and rotation phases (3, 6, 7, 8) turn on design decisions at
the guest/host boundary and are planned at high effort. Phase 1 is
high effort because everything downstream rests on its measurements.
Phases 5, 10, 11 and 12 follow patterns already established by
`PLAN-bat-population.md` and `PLAN-create.md` and can be planned at
medium. Phases 14, 15 and 17 are mechanical.

### Verification that is not optional

* **Cite or measure.** No phase asserts a qemu-img behaviour without
  either a measurement in this plan or a citation to qemu source. The
  management session re-runs a sample of any sub-agent's measurements.
* **Never trust a single green run.** The integration suite is
  zero-fail on healthy testdata, but a `test_convert` cluster on this
  host reporting "Content mismatch at offset 0" or "convert operation
  failed" is 16-worker contention, not a regression; replay at low
  concurrency with `--select` before attributing it.
* **Run the consumer suites.** A change to a crate can alter
  non-gated behaviour in the operations that link it; crate tests
  alone are not enough.
* **Re-measure the binary sizes.** Phases 6 and 7 change core's
  `.bss`. `scripts/check-binary-sizes.sh` is part of their definition
  of done.
* **Mutation-check the guards.** Where a phase adds a test that is
  meant to prove a refusal or a bound, prove the test would notice its
  absence.

## Administration and logistics

### Success criteria

1. `instar create -f vmdk -o subformat=twoGbMaxExtentSparse` and
   `...=twoGbMaxExtentFlat` produce images byte-identical to
   qemu-img's for every measured geometry, or divergent only where a
   divergence is documented and justified.
2. `instar convert -O vmdk --subformat twoGbMaxExtent*` likewise.
3. `instar measure` reports what qemu-img reports for both subformats.
4. instar reads a `twoGbMaxExtent*` image with more than 15 extents,
   and reads `twoGbMaxExtentSparse` sources at all.
5. The size at which instar refuses a multi-extent image matches
   qemu-img's, and the refusal leaves nothing behind.
6. A failure part-way through writing a multi-extent image never
   leaves a descriptor pointing at a set of extents that does not
   match it.
7. The differential fuzzer compares multi-extent output structurally,
   not just through `qemu-img info`, and the new options are drawn.
8. `make test` and the integration suite are zero-fail, with "Ran: N"
   checked rather than the exit code trusted.

### Documentation index maintenance

This plan is registered in `docs/plans/index.md` and `docs/plans/order.yml`
when it is created, and its status there is kept in step with the
Status line above.

### Future work

Filled in as the plan runs. Already known:

* Retrofitting `ddb.*` onto `monolithicSparse`, `streamOptimized` and
  `monolithicFlat` descriptors (open question 3), which churns every
  existing VMDK baseline and wants its own differential-fuzz exposure.
* `twoGbMaxExtent*` as a backing file or in a backing chain, excluded
  from scope above.
* `instar map` on flat sources, which is refused today for all flat
  sources and is wider than this plan.
* Making `tests/helpers/vmdk_structure.py` check an image against
  itself, rather than only against another image. It never compares an
  extent line's declared sector count with the sparse header's capacity
  or with a flat extent's file length, so an image whose descriptor and
  extents disagree parses cleanly and is caught only if the image it is
  compared against happens to be right. Raised in review on
  [#921](https://github.com/shakenfist/instar/pull/921) and left to the
  phases that use the parser as an oracle (10, 11 and 16), because what
  counts as internally consistent is a property of the writer those
  phases build, and a refusal added now would be guessing at it. The
  same entry covers two neighbours raised in the same review: a start
  offset is carried through on any extent kind although only `FLAT`
  lines are supposed to have one, and a `ZERO` extent that carries a
  filename is opened and measured as though it held data. No writer
  produces either, so each is a rule about malformed input rather than
  a defect against anything that exists.
* Three VMDK fixtures, `vmdk-multi-extent`, `vmdk-flat-parent` and
  `vmdk-flat-with-parent`, carry `skip_qemu_img` for no recoverable
  reason. Measured with qemu-img 10.2.0, it opens all three
  successfully (rc=0; `vmdk-multi-extent` reports 2 GiB without opening
  its absent extents). Only `vmdk-twogb-flat` is genuinely refused
  (rc=1, "Could not open ... vmdk-twogb-flat-f001.vmdk: No such file or
  directory"). `git log -S` puts each flag in the commit that created
  its fixture with no explanation, so the original reason is
  unrecoverable, and the flag costs those three images every qemu
  baseline they could have.
* `info --ignore-quirks` has no test coverage anywhere in the suite; no
  test file mentions it. This work changed its behaviour for a
  multi-file VMDK (it now sums the extents' apparent lengths rather than
  printing the descriptor's length on every line), and nothing would
  have caught that being wrong.
* `tests/test_convert.py`'s `_test_vhd_convert` asserts `cmp_rc == 0`
  and reports any other value as "differs from qemu-img", but 4 is
  `COMPARE_EXIT_READ_ERROR` (`src/vmm/src/main.rs`), not the exit code
  for a content difference, and the helper discards compare's stderr. A
  load-induced read error therefore reads as a parity failure with an
  empty message, which has cost real debugging time.
* The human-format path of `tests/helpers/comparators.py` treats every
  `disk size:` line after the first `Child node '` line as belonging to
  a child. That is correct for one image's output and wrong for the
  concatenated text of several, which `info --chain` human output would
  be. No baseline comparison feeds it such text today, so this is latent
  rather than live.
* A VMDK descriptor's extents are capped at `MAX_CHAIN_EXTENTS` (64,
  set by phase 3), which is 128 GiB at qemu's 2 GiB default. qemu has no
  equivalent limit. The bound is the 4096-byte config page: `ChainConfig`
  sits at `CHAIN_CONFIG_ADDR` and `VMM_PARAMS_ADDR` begins one page
  later, so lifting it means moving the config region rather than
  changing a constant.
* An extent line with a non-zero start offset is refused
  (`src/vmm/src/chain.rs:967`). qemu's split subformats give each extent
  its own file at offset 0, so nothing this plan writes or reads needs
  it; a monolithic multi-extent descriptor that packs extents into one
  file at offsets would still be refused.

### Bugs fixed during this work

Filled in as the plan runs.

* [#934](https://github.com/shakenfist/instar/issues/934), a VMDK with
  more extents than the 16 device slots cannot be read -- the read-side
  ceiling measured in the Situation, filed when phase 3 was planned.
  Phase 3 fixes it by making a descriptor's extents cost one slot
  between them instead of one each. The issue also records a trap for
  whoever changes the slot count: the virtqueue-region assert at
  `src/shared/src/lib.rs:664` hard-codes its own `16` rather than
  deriving it from `MAX_CHAIN_DEVICES`, so raising the constant does not
  trip it.

* [#922](https://github.com/shakenfist/instar/issues/922), `info`
  reports the wrong allocation and child order for a multi-file VMDK.
  Three divergences, all on a VMDK whose descriptor resolves to
  separate extent files, `monolithicFlat` as much as the
  `twoGbMaxExtent*` split subformats:
  * The top-level allocation figure was the descriptor's own block count
    alone, where qemu-img reports the sum over the descriptor and every
    extent; `vmdk-multi-flat` read 4096 bytes against qemu's 1576960.
  * The human-format rendering printed the descriptor's allocation on
    every per-extent child line, instead of that extent's own.
  * `children[]` was emitted in descriptor order and unconditionally,
    bypassing the version gate, so `--qemu-version 7.2` produced an
    array that no qemu build before 8.0.0 emits. qemu walks the extents
    from last to first, keeping each index, descriptor afterwards.

  All three are fixed on the phase 2 branch, together with the test
  helper that was masking the first one.

### Back brief

Before implementation begins, the implementing session restates this
plan's mission, the resolved open questions, and the phase it is
starting, in its own words, and the operator confirms. Questions 1 and
2, which change the shape of the whole plan, were resolved on
2026-11-16; questions 3 to 6 remain open, and phase 1's plan file names
which of them its measurements settle.
