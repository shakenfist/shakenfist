# PLAN: Differencing phase 7 — guest create op and host CLI wiring

Phase 7 of [PLAN-differencing.md](/components/instar/plans/PLAN-differencing/).

## Goal

Make `instar create -f vpc -b parent.vhd child.vhd` and
`instar create -f vhdx -b parent.vhdx child.vhdx` actually produce a
differencing child. Phases 5 and 6 built the two emitters; both are
unreachable behind an explicit refusal in the guest create op,
because neither could be handed a real parent identity. This phase
reads that identity off the parent and takes the guards off.

The identity is the parent's VHD footer `uuid` plus its `timestamp`
for VHD, and the parent's active-header `DataWriteGuid` for VHDX.
The proof that this phase worked is a round trip through instar's own
phase 3 parsers and through `vhdiinfo`, not an assertion that the
bytes were written.

## Planning effort

**High**, as [PLAN-differencing.md](/components/instar/plans/PLAN-differencing/) specifies:
the phase touches the guest/host boundary. The change itself is
small — smaller than the master plan expected, for reasons the
survey sets out — but the judgement calls are not.

## Review effort

**High.** Three of this phase's decisions are only checkable against
format specs and measured oracle behaviour: where a VHD footer
actually sits when the sector size is not 512, whether a POSIX path
may be written under a Windows-defined locator key, and what
`create -b` should do when the parent is not the format the child
claims. A plausible-looking wrong answer to any of them produces a
well-formed image that no consumer can resolve.

## Scope

**In scope:**

* `vhdx::VhdxHeader` gains `data_write_guid`. The offset constant
  already exists and the parser already reads the surrounding
  fields; only the field itself is missing.
* The guest's backing probe returns the parent's identity alongside
  the virtual size it already computes, rather than a second pass
  over the same sectors.
* The latent VHD footer location bug the survey measured
  (finding 3) is fixed, because this phase's identity read rides on
  exactly the sector that bug mislocates. It is not reachable from
  the CLI today -- `create` is restricted to 512-byte sectors -- so
  this is robustness, not a user-facing fix.
* A typed error when the parent's format does not match the child's,
  resolving the master plan's open question 7.
* Issue #570 — POSIX paths under Windows-defined locator keys — is
  settled and applied to both emitters. The issue's own stated
  timing is "decide before the guard in the guest create op is
  removed", and this is the phase that removes it.
* The two refusal guards come off, and
  `test_create_vhd_and_vhdx_reject_backing` becomes a round-trip
  test rather than being deleted.
* `docs/create.md`, `docs/quirks.md` and `docs/format-coverage.md`
  stop describing a refusal that no longer happens.

**Out of scope:**

* Giving created images a real identity (#566). It needs a
  host-side entropy source through the call table — the one ABI
  change this plan is built to avoid. The consequence for this
  phase is recorded in decision 8.
* Composing a parent's sector data on read (phases 11-16). A child
  this phase writes is a valid differencing image that instar's own
  read paths still refuse, exactly as phase 4 decided.
* Host-side chain discovery and `info --chain` (phase 11).
* Overlapping metadata item detection (#576).
* The full test matrix. Phase 8 owns it; this phase carries only
  the tests that prove its own steps, plus the conversion of the
  existing guard test.

## What the survey found

The master plan's phase 7 section was written before phases 5 and 6
executed. Six of its claims needed correcting, and two of the
corrections make the phase materially smaller than planned. All
corrections have been applied at source in
[PLAN-differencing.md](/components/instar/plans/PLAN-differencing/) and
[index.md](/components/instar/plans/index/) as part of this phase's first commit, so a
later step does not need to redo them.

**1. Line references had drifted by one.** The master plan cites
`run_create_nonraw` at `src/vmm/src/main.rs:16815` and the typed
backing region at `:16913-16963`. They are at `:16816` and
`:16914-16964`. The substance was right: the host does open the
host-resolved parent and attach it as input device 0, and it does
embed `typed_backing.as_bytes()` verbatim rather than the resolved
path (`:16962-16973`).

**2. The mechanism already exists and is already proven in this
binary.** The master plan says the guest can read the parent
"without any new call-table primitive". That is true and
understates it: `read_vmdk_parent_cid`
(`src/operations/create/src/main.rs:286`) already reads a parent
through `(call_table.read_input_sector)(0, …)` and feeds the
identity it extracts into a planner, gated on the declared backing
format. Phase 7 is applying an established in-tree pattern twice,
not inventing one.

Stronger still: `read_backing_virtual_size`
(`src/operations/create/src/main.rs:152`) **already parses both
parents**. Its `ImageFormat::Vhd` arm constructs a
`vhd::VhdFooter` — whose `uuid` and `timestamp` fields are exactly
what `plan_vhd` needs — and discards everything but
`current_size`. Its `ImageFormat::Vhdx` arm runs
`vhdx::VhdxState::init`, which performs active-header selection,
and keeps only `virtual_disk_size` and `has_parent`. The identity
this phase needs is already being read and thrown away.

**3. A measured — but latent — bug on `develop` sits in the code
this phase must touch.** `read_backing_virtual_size`'s VHD arm
reads the last sector and parses the footer at **offset 0 of that
sector**:

```rust
if !(call_table.read_input_sector)(0, capacity - 1, header_ptr, sector_size) {
    return Err(PARSE_FAILED);
}
let last_sector = core::slice::from_raw_parts(header_ptr, sector_size);
let footer = vhd::VhdFooter::parse(last_sector).ok_or(PARSE_FAILED)?;
```

`get_input_capacity` returns a count of **sectors, rounded up** —
`capacity: size_bytes.div_ceil(sector_size)` in
`src/vmm/src/virtio/block.rs:132`, with reads past EOF zero-padded.
A VHD footer is the last 512 bytes of the file. Those two facts
agree only when the sector size is 512.

Measured against qemu-img output on this host (`create` takes a
`--sector-size`, and the table shows where the footer would be found
at each value):

| Parent | Size | ss | capacity | last sector starts at | footer at | bytes found |
|--------|------|----|----------|----------------------|-----------|-------------|
| dynamic 64M | 2560 | 512 | 5 | 2048 | 2048 | `conectix` |
| dynamic 64M | 2560 | 4096 | 1 | 0 | 2048 | `conectix` (the offset-0 **copy**, by luck) |
| dynamic 64G | 133632 | 4096 | 33 | 131072 | 133120 | `ffffffffffffffff` — **fails** |
| fixed 8M | 8391168 | 4096 | 2049 | 8388608 | 8390656 | zeros — **fails** |

**The bug is not reachable from the CLI today, and an earlier
draft of this plan wrongly said it was.** `create` refuses any
sector size but 512 (`src/vmm/src/main.rs:17905`, "must be 512 in
phase 3"), a restriction `PLAN-create.md` phase 5 plans to lift.
Running `instar create -f qcow2 -b big.vhd --sector-size 4096`
against a binary built from `develop` returns that refusal, not a
parse error -- checked, after the claim was written, rather than
assumed.

So this is latent rather than live: the arithmetic in the table is
wrong today and produces a wrong answer the moment the 512-only
restriction comes off. It still cannot be left alone, because the
parent-identity read this phase adds needs exactly those 512 bytes
and would inherit the same mislocation. It is fixed here as
robustness, not as a user-facing bug fix, and 7g's changelog entry
should not claim otherwise.

VHDX is unaffected either way: its headers live at fixed 64 KiB and
128 KiB offsets, which every legal sector size divides.

**4. `VhdxHeader` drops the field this phase needs.**
`HEADER_DATA_WRITE_GUID_OFFSET = 32` is already a public constant
(`src/crates/vhdx/src/lib.rs:118`) and `VhdxHeader::parse`
(`:249`) already reads `log_guid` from 16 bytes further on, but the
struct (`:235-242`) carries `signature`, `checksum`,
`sequence_number`, `log_guid`, `log_length` and `log_offset` — and
no `data_write_guid`. Neither state struct exposes parent identity
either: `VhdState` has no `uuid` or `timestamp` field, and
`VhdxState` has no `data_write_guid`.

**5. The binary budget is a non-issue, contrary to the master
plan's constraint note.** That note says the new guest code "wants
budgeting". The built `create` guest binary is **65,224 bytes
against a 768 KiB cap** — 8.5% — and both the `vhd` and `vhdx`
crates are *already* dependencies of the create op
(`src/operations/create/Cargo.toml`), so this phase links no new
crate. `make check-binary-sizes` remains a regression check, not a
budget question.

**6. The master plan contradicts itself on backing-format
validation.** Its phase 7 paragraph frames the question as open —
"phase 7 should either refuse a mismatched hint or state in the
planner's contract that format compatibility is the caller's
business" — but open question 7 was RESOLVED on 2026-09-05:
"**yes, with a typed error otherwise** … phase 7 wires the error."
The paragraph was written during phase 6's review round without
reference to the resolved question. Corrected at source; decision 4
below implements the resolution, and improves on it, because the
survey also found that `config.backing_format` is only populated
when the user passes `-F` (`src/vmm/src/main.rs:16975-16977`)
while the guest can *detect* the parent's real format from its
header regardless.

**7. The probe only runs when no size was given, so every check on
the parent is skippable.** Found while reviewing 7c. The call site
reads:

```rust
let virtual_size: u64 = if config.virtual_size != 0 {
    config.virtual_size
} else if config.has_backing() {
    match probe_backing(call_table, config.sector_size as usize) {
```

So `instar create -f qcow2 -b differencing.vhd -F vpc child.qcow2 64M`
never opens the parent, and phase 4's `ERROR_BACKING_DIFFERENCING`
refusal does not fire -- demonstrated against a `develop` binary with
the phase 2 fixture, where the same command without the trailing
`64M` is correctly refused. `ERROR_BACKING_PARSE_FAILED` and the
format detection are skipped by the same path.

This is pre-existing and wider than differencing, so it is filed as
**#579**. It lands in this phase's scope regardless, because the
parent identity must be available whenever `-b` is given and decision
4's format check would otherwise inherit the identical hole. 7f
restructures the call site to probe whenever `config.has_backing()`
and to prefer an explicit size over the probed one.

**Nothing else in the phase 7 section was wrong.** The claim that
makes this plan tractable — that the host already attaches the
parent as input device 0 whenever `-b` is given, so no call-table
change is needed — holds exactly as written.

## Decisions

**1. Extend the existing probe; do not add a second reader.**
`read_backing_virtual_size` already reads and parses both parents
(finding 2). It becomes `probe_backing`, returning a small
`BackingProbe { virtual_size, format, identity }` rather than a
bare `u64`. The alternative — a `read_vhd_parent_identity` and a
`read_vhdx_parent_data_write_guid` alongside it, mirroring
`read_vmdk_parent_cid` — would read the same sectors a second time
and give two places that must agree about where a footer lives.
`read_vmdk_parent_cid` stays as it is: it reads a *descriptor* at a
sector the header points to, which the probe does not touch.

**2. Locate the VHD footer by scanning back from end-of-data, not
by assuming it starts the last sector.** The guest cannot recover
the parent's exact byte size: `get_input_capacity` is `div_ceil`,
so a capacity of *n* means the size lies in `((n-1)·ss, n·ss]`. The
fix is to read the last sector and scan **backwards in 512-byte
steps** for the `conectix` cookie, taking the last match. A VHD's
size is always a multiple of 512 and the footer is always the final
512 bytes, so the footer begins at one of at most `ss/512`
candidate offsets — eight for a 4096-byte sector — and the trailing
zero padding past EOF cannot produce a false match because it is
not the cookie.

Rejected: reading the footer copy at offset 0 instead. It exists
only for dynamic VHDs; a fixed parent has data there, and the
survey measured exactly that failure.

Rejected: passing the parent's byte size through `CreateConfig`.
That is an ABI change, and this plan is built to avoid those.

**3. Identity comes from the parent, and the timestamp comes from
the parent's footer.** `plan_vhd`'s `parent_timestamp` is the
parent's own creation timestamp, not the moment the child was
created — it exists so a reader can tell whether the parent it
found is the parent that was linked. Using wall-clock time would
make the field vacuous, and the guest has no clock anyway.

**4. Detect the parent's format; do not trust `-F`.** Open question
7 requires a typed error when a child's parent is a different
format. The hint is the wrong thing to check: it is absent unless
the user passed `-F`, and the guest already calls
`detect_format_from_header` on the parent's first sector. So:

* Detection decides. A vpc target whose parent detects as anything
  but VHD is refused; likewise vhdx and VHDX.
* If `-F` was given **and contradicts detection**, refuse as well —
  the user asserted something the bytes disprove, and silently
  preferring the bytes hides a mistake worth surfacing.
* Formats other than vpc and vhdx are untouched. qcow2 and vmdk
  accept mixed-format parents today and this phase does not change
  that.

**5. One new `CreateResult` code, 13.** The codes are an
append-only ABI duplicated between `src/shared/src/lib.rs` and a
mirrored set of consts in `src/vmm/src/main.rs`, with no
compile-time cross-check, so each addition is an edit nothing
verifies.

Two corrections, both from 7d checking rather than assuming.
`map_create_error` is **not** a third site for this code: it maps
`crates/create`'s `CreateError` variants, and every code about the
*parent probe* -- `ERROR_BACKING_READ_FAILED`,
`ERROR_BACKING_PARSE_FAILED`, `ERROR_BACKING_DIFFERENCING` and the
rest -- is returned as a raw `u32` from `probe_backing` or its call
site without passing through `CreateError` at all. The mismatch
check happens at the call site before `plan_vhd`/`plan_vhdx` runs,
so it follows that same pattern and needs no variant.

And the host message cannot name the formats involved:
`create_error_detail` (`src/vmm/src/main.rs:17531`) is
`fn(code: u32) -> &'static str`, with no payload channel, so every
message in that table is static text. The message states the rule
generically instead. Giving the guest a way to return values
alongside a code would be an ABI change, which this plan avoids;
worth an issue rather than a phase 7 step. `ERROR_PARENT_FORMAT_MISMATCH = 13` is the only addition
this phase needs: the read and parse failures already have
`ERROR_BACKING_READ_FAILED` (4) and `ERROR_BACKING_PARSE_FAILED`
(5), and a differencing parent already has
`ERROR_BACKING_DIFFERENCING` (11).

**6. Issue #570 — normalise relative paths, keep absolute ones
verbatim, document the split.** This is the decision most likely to
be argued with, because phase 6 decided option 3 (keep everything
as is) for the same question five days ago.

What changed is that phase 6 was an emitter-only phase where
nothing user-reachable produced the bytes, and #570's own
"Suggested timing" section says to decide before the guard comes
off. This is that moment.

The answer splits because the two cases are not equally
expressible:

One consequence to carry into the documentation: a relative parent
path caps two code units shorter than an absolute one, because the
`.\` prefix is spent from the same budget.

* **Relative paths can be written honestly in the Windows
  convention.** `.\parent.vhd` means the same file on both
  platforms, and it is what the measured Hyper-V fixtures from
  phase 3 actually contain. So VHD's `W2ru` and VHDX's
  `relative_path` get `/` → `\` and a leading `.\`. This costs
  nothing: instar must already parse that convention to read
  Hyper-V children — the phase 3 fixtures are Hyper-V — so the
  un-normalisation phase 11 needs is code phase 11 owes the
  fixtures regardless. Emitting the same dialect it reads keeps
  instar's writer and reader symmetric instead of inventing a third
  dialect only instar produces.
* **POSIX-absolute paths cannot.** `/srv/images/parent.vhd` has no
  honest `W2ku` or `absolute_win32_path` rendering; fabricating a
  drive letter would be worse than the divergence. VHD can fall
  back on the parent unicode name — the field qemu's `block/vpc.c`
  and libvhdi actually read — but SPEC(VHDX) 2.6.2.6.3 requires at
  least one of the three path keys, and the third, `volume_path`,
  needs a Windows volume GUID no Linux producer can obtain. So both
  formats keep the verbatim POSIX bytes under the absolute key, and
  `docs/quirks.md` records it.

A reviewer may reasonably prefer option 2 for the absolute case —
emit no locator at all for VHD, relying on the unicode name. It is
honest and loses nothing either oracle reads. It is rejected only
because it makes VHD and VHDX disagree about the same input, and
#570's closing note asks for both to be settled the same way.

**7. The guard test converts rather than disappears.**
`test_create_vhd_and_vhdx_reject_backing` (`tests/test_create.py:248`)
was added during phase 6's review round and is the only test
covering either guard. Deleting it when the guards come off would
silently drop the coverage; it becomes
`test_create_vhd_and_vhdx_differencing_round_trip`, asserting the
child is created, that `instar info` reports it as differencing,
and that the parent identity in the child matches the parent's own.

**8. The negative identity test needs a third-party parent, and
this phase does not write one.** Because of #566 every image instar
creates shares one identity, so a child instar wrote against a
parent instar wrote would pass an identity check that compares
zeros to zeros. This phase's tests therefore use the phase 2
Hyper-V fixtures as parents wherever they assert identity
*matching*, and phase 8 owns the systematic version. Stated here so
a reviewer does not read a passing same-identity test as proof.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7a | medium | sonnet | none | Add `pub data_write_guid: [u8; 16]` to `VhdxHeader` (`src/crates/vhdx/src/lib.rs:235`) and populate it in `VhdxHeader::parse` (`:249`) from `HEADER_DATA_WRITE_GUID_OFFSET` (`:118`, value 32), copying the `log_guid` lines at `:266-267` exactly — same 16-byte `copy_from_slice`, same ordering. The crate is `no_std` and panic-free: `parse` has already bounds-checked `buf.len() >= HEADER_SIZE` before this point, so no new check is needed. Add one unit test asserting the GUID read back from a buffer built by `build_header` (`:2250`) equals the bytes that function writes at offset 32 — `build_header` derives the DataWriteGuid from the sequence number (`:2257-2261`), so the test must not hardcode a GUID. Do not touch `VhdxState`. Commit subject: "Expose data_write_guid on VhdxHeader." |
| 7b | high | opus | none | Fix the VHD footer mislocation in `read_backing_virtual_size` (`src/operations/create/src/main.rs:152`, VHD arm at `:174-186`). It reads the last sector and parses the footer at offset 0 of it, which is only correct when `sector_size == 512`; `get_input_capacity` is `div_ceil(size_bytes, sector_size)` (`src/vmm/src/virtio/block.rs:132`) and reads past EOF zero-pad. Replace the parse with a backward scan over the last sector in 512-byte steps, taking the **last** offset whose 8 bytes equal `vhd::VHD_COOKIE` (`conectix`) and parsing there; return `ERROR_BACKING_PARSE_FAILED` if none matches. Read decision 2 and survey finding 3 before starting. Note that the bug is **latent**: `create` refuses any sector size but 512 (`src/vmm/src/main.rs:17905`), so it is not reachable from the CLI and your tests must be Rust tests exercising the scan directly, not integration tests driving the binary. Cover sector sizes 512 and 4096 against both a dynamic parent (footer copy at offset 0, real footer at the tail) and a fixed one (no copy); the 4096 fixed case must fail before this change and pass after. Prove each new test can fail by mutating the scan to a forward scan and to a fixed offset 0, and report both results. Commit subject: "Locate the VHD footer independently of sector size." |
| 7c | high | opus | none | Rework `read_backing_virtual_size` into `probe_backing` returning `Result<BackingProbe, u32>` where `BackingProbe { virtual_size: u64, format: ImageFormat, identity: ParentIdentity }` and `ParentIdentity` is an enum of `None`, `Vhd { uuid: [u8; 16], timestamp: u32 }` and `Vhdx { data_write_guid: [u8; 16] }`. The VHD arm already builds a `vhd::VhdFooter` whose `uuid` and `timestamp` fields are what is wanted (`src/crates/vhd/src/lib.rs:182-199`); the VHDX arm runs `vhdx::VhdxState::init`, which selects the active header internally (`src/crates/vhdx/src/lib.rs:1647-1658`) but does not retain it, so read header 1 and header 2 directly at `HEADER1_OFFSET`/`HEADER2_OFFSET` with `VhdxHeader::parse` (7a's field) and take the higher `sequence_number`, matching that selection rule exactly — including its tie-break, which prefers header 1 on equality. Update the single call site (`:554`). Every existing behaviour must be preserved: the differencing refusals, the raw capacity multiply, and each error code. Commit subject: "Return the parent's identity from the backing probe." |
| 7d | medium | sonnet | none | Add `ERROR_PARENT_FORMAT_MISMATCH: u32 = 13` to `CreateResult` (`src/shared/src/lib.rs:3492-3519`, currently ending at 12), with a doc comment saying a differencing child must share its parent's format, per Hyper-V. Codes are an append-only ABI duplicated in three places with no compile-time cross-check, so also add the mirrored const and the host-side message in `src/vmm/src/main.rs` beside the `ERROR_PARENT_NAME_TOO_LONG` arm. Note `create_error_detail` is `fn(u32) -> &'static str`, so the message cannot name the actual detected format — state the rule generically (a vpc child needs a VHD parent, a vhdx child a VHDX one) rather than trying to interpolate. No `map_create_error` arm and no `CreateError` variant: see decision 5. Do not wire any caller; 7f does that. Commit subject: "Add a typed parent format mismatch error." |
| 7e | high | opus | worktree | Implement decision 6 (read it in full first) in both emitters: `create::plan_vhd`'s platform-code selection and `create::plan_vhdx`'s path-key selection. A **relative** path is normalised for emission — `/` becomes `\`, and a leading `./` or no prefix becomes `.\` — matching the measured Hyper-V fixtures from phase 3. An **absolute POSIX** path is written verbatim under `W2ku` / `absolute_win32_path`, unchanged from today. Normalisation happens on a copy in the emitter, never to the VHD parent unicode name field, which keeps the typed bytes because that is the field qemu and libvhdi resolve through. Watch the length limits: normalisation can add two bytes (`.\`), so a check must run on the **normalised** string, and the existing boundary tests need their expectations rechecked rather than their numbers adjusted to fit. Note VHD has *two* limits, not one -- 255 code units on the parent unicode **name** field, which carries the path as typed and is therefore unaffected by normalisation, and one sector (256 code units) on the **locator**, which carries the normalised string. The smaller binds, so a relative VHD path caps at 254 typed. VHDX has the single 260 limit on the emitted string, so relative caps at 258. Isolation is a worktree because this changes bytes phases 5 and 6 pinned with golden tests; those tests must be updated deliberately and each change explained, not regenerated. Commit subject: "Emit Hyper-V path conventions for relative parents." |
| 7f | high | opus | none | **First** restructure the probe call site (`src/operations/create/src/main.rs:642-650`) per survey finding 7 and #579: call `probe_backing` whenever `config.has_backing()` rather than only when `config.virtual_size == 0`, and prefer an explicit size over the probed one. Without this the identity is unavailable, and the format check of decision 4 is silently skipped, whenever the user passes a size. Then remove the two `if backing_ref.is_some()` guards in the create op's `ImageFormat::Vhd` and `ImageFormat::Vhdx` arms (`src/operations/create/src/main.rs:697-703` and `:729-735`) and their now-stale comments. Feed 7c's identity into `vhd_opts_from` (`:311`) and `vhdx_opts_from` (`:~350`), replacing the `[0u8; 16]` placeholders at `:334-335` and `:358` and the comments above them that say the guest has no way to read a parent. Before `plan_vhd`/`plan_vhdx` runs, apply decision 4: refuse with `ERROR_PARENT_FORMAT_MISMATCH` when the probe's detected format is not VHD for a vpc target or VHDX for a vhdx target, and also when `config.backing_format` is set and disagrees with detection. Then convert `test_create_vhd_and_vhdx_reject_backing` (`tests/test_create.py:248`) per decision 7 into a round-trip test. Prove the new test can fail: neuter the identity plumbing back to zeros and confirm it fails on the identity assertion rather than on image creation, and report that result. Commit subject: "Create differencing VHD and VHDX children." |
| 7g | medium | sonnet | none | Documentation, no code. `docs/create.md`: the vpc and vhdx `backing_file` bullets now describe a supported operation, not a refusal — mirror how the qcow2 bullet reads, and state that the parent must be the same format. `docs/quirks.md`: add the decision 6 split to the VHD/VHDX differencing section — relative parents emit Hyper-V conventions, POSIX-absolute parents keep their bytes under a Windows-defined key, and why. State the resulting asymmetry: a relative parent path caps two code units shorter than an absolute one (254 vs 255 for VHD, 258 vs 260 for VHDX) because the `.\` prefix comes out of the same budget. `docs/format-coverage.md`: differencing output is a recorded divergence from qemu-img per open question 4, so add it as a note in the style of note 8. `CHANGELOG.md`: an `### Added` entry, user-facing this time — unlike phases 5 and 6, this one changes what the CLI does. It needs a `### Fixed` entry too, for #579: every backing-image check now runs when an explicit size is given, where before all of them were skipped. Note the one behaviour change that reaches beyond differencing — a backing image in a format the probe cannot parse (vdi, qcow1, qed, iso, luks) is now refused with an explicit size as well as without, where `develop` accepted it. Per AGENTS.md, no phase numbers in documentation: link [PLAN-differencing.md](/components/instar/plans/docs/plans/PLAN-differencing/) instead. Commit subject: "Document differencing image creation." |

## Risks and mitigations

* **7c's rework is on the critical path of every `create -b`, not
  just differencing ones.** A regression there breaks qcow2 and
  vmdk backing too. *Mitigation:* the brief requires the existing
  error codes and refusals to be preserved exactly; the management
  session diffs `probe_backing` against the original arm by arm
  before accepting, and the existing `create -b` integration tests
  must pass unchanged.
* **7b's scan could mask a genuinely corrupt parent** by finding a
  stale cookie earlier in the sector. *Mitigation:* take the
  **last** match, not the first — the real footer is the final 512
  bytes of the file, and every 512-aligned slot after it is
  zero-padding past end-of-file, which cannot carry the cookie.

  An earlier draft of this item also claimed `VhdFooter::parse`
  validates the footer checksum and would reject a stray cookie. It
  does not: `parse` (`src/crates/vhd/src/lib.rs:206`) checks only
  the buffer length and the cookie, reads `checksum` into the struct
  and never verifies it — `compute_checksum` (`:1055`) is called
  only by the writers and by tests. **Taking the last match is the
  only defence there is**, which is why 7b pins it with a test that
  places three valid footers in one sector. Corrected here after 7b
  checked the claim rather than inheriting it.
* **7e changes bytes that phases 5 and 6 pinned with golden
  tests.** A sub-agent that regenerates a golden to match its
  output destroys the value of the test. *Mitigation:* worktree
  isolation, and the management session reviews every golden change
  as a deliberate diff with a stated reason.
* **The 768 KiB guest cap.** Judged a non-issue by measurement
  (finding 5: 65 KiB used), but this phase links more of the `vhd`
  and `vhdx` crates than before. *Mitigation:*
  `make check-binary-sizes` in the definition of done, as a
  regression check.
* **#566 makes same-identity assertions vacuous.** *Mitigation:*
  decision 8 — identity-matching assertions use third-party
  parents from the phase 2 fixtures, and the limitation is stated
  in the test's own comment so a later reader is not misled.
* **Removing the guards is the moment every deferred locator
  question becomes user-visible.** *Mitigation:* #570 is settled in
  this phase by decision 6 rather than inherited, which is the
  timing the issue itself asks for.

## Definition of done

* `instar create -f vpc -b parent.vhd child.vhd` and
  `instar create -f vhdx -b parent.vhdx child.vhdx` both exit 0 and
  write a child, against a binary built from this branch.
* The VHD child's footer `disk_type` is 4 and its parent unique id
  equals the parent's footer `uuid`; the VHDX child's File
  Parameters flags are `0x00000002` and its `parent_linkage` equals
  the braced rendering of the parent's active-header
  `DataWriteGuid`. Asserted against a **third-party** parent, per
  decision 8, so the comparison is not zeros against zeros.
* `instar info` on the VHD child reports `backing-filename` and
  `backing-filename-format`, and `vhdiinfo` on the VHDX child
  reports a parent locator whose linkage matches — the phase 6
  plan's libvhdi `relative_path` caveat applies to the "Parent
  filename" line only.

  **Not** `qemu-img info` for the VHD child: an earlier draft of
  this item asked for that, and it cannot pass. `block/vpc.c` does
  not surface a VHD parent at all — checked against the genuine
  Hyper-V fixture `vhd-diff-child-mixed.vhd`, which qemu-img also
  reports with no backing file. That is the premise the master plan
  already states, that qemu "reads both as though the parent did
  not exist"; this item had contradicted it.
* 7b's footer-location tests pass at sector sizes 512 and 4096
  against both a dynamic and a fixed parent. The 4096 fixed case
  fails before 7b and passes after -- that pair is the falsifier,
  and it is a Rust test rather than a CLI invocation because
  `create` refuses a 4096-byte sector today (finding 3).
* `create -f vpc -b parent.vhdx` and `create -f vhdx -b parent.vhd`
  both fail with the mismatch message, exit non-zero, and write no
  child. Likewise `-f vpc -b parent.vhd -F qcow2`, where the hint
  contradicts the bytes.
* Every check reached through the probe fires **with** an explicit
  size as well as without: `create -f qcow2 -b <differencing parent>
  child.qcow2 64M` is refused, which it is not on `develop` today
  (#579). Asserted by an integration test, since the op itself is not
  in the tested workspace.
* No comment in `src/operations/create/src/main.rs` says the guest
  cannot read a parent, and no `[0u8; 16]` literal there stands in
  for an identity the guest failed to read. Two such literals do
  remain, and correctly: they are the no-parent arms of the
  identity match, where the planner writes no parent fields at all.
  Removing them would mean making the fields `Option` in
  `crates/create`, which is a separate change.
* A relative `-b parent.vhd` produces a locator entry whose string
  is `.\parent.vhd`; an absolute `-b /srv/x/parent.vhd` produces
  `/srv/x/parent.vhd` verbatim. Both asserted on emitted bytes.
* Every golden-byte change from 7e is explained in its commit
  message, and no golden was regenerated without one.
* `make test-rust`, `make check-binary-sizes`, `make lint` and
  `pre-commit run --all-files` are all clean, and
  `make test-integration` passes the create suite.
* The mutation script carried from phases 5 and 6 runs, every
  mutation is caught, and the running count is stated in the pull
  request.
* `docs/create.md` contains no claim that vpc or vhdx refuses a
  backing file, and `docs/quirks.md` states the decision 6 split.
* #570 is closed with the decision recorded on it, and #566 is
  updated to note that its identity gap is now reachable by users
  rather than only by crate callers.

## Back brief

Before starting, restate: which of the six survey findings changes
what you were going to do, and what decision 6 requires you to emit
for `-b ./parent.vhd` versus `-b /srv/parent.vhd`.

**Gate before 7e.** Stop after 7d and report. 7e changes bytes that
two prior phases pinned with golden tests, and #570's resolution is
the decision in this plan most likely to be overturned on review —
settling it wrongly is cheap to propose and expensive to unwind
once goldens have moved. Do not begin 7e until decision 6 is
confirmed.

**Gate before 7f.** 7f is the step that makes every prior phase
user-reachable. Confirm before starting that 7c's identity actually
reaches `vhd_opts_from` and `vhdx_opts_from` with the parent's
bytes and not the placeholder, and that the mismatch refusal is in
place — a child written with a zero identity is a well-formed image
that silently resolves to the wrong parent.
