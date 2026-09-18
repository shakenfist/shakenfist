# PLAN: Differencing phase 6 — the `plan_vhdx` differencing emitter

Phase 6 of [PLAN-differencing.md](/components/instar/plans/PLAN-differencing/).

## Goal

Make `create::plan_vhdx` able to emit a differencing VHDX: the
File Parameters `HasParent` bit set, and a parent locator metadata
item carrying `parent_linkage` (the parent's `DataWriteGuid`) plus
exactly one path key.

This phase is the emitter only, exactly as phase 5 was. It does
not make `instar create -f vhdx -b parent.vhdx` work: the guest
has nowhere to get the parent's `DataWriteGuid` from until phase 7
opens the parent, so the `ImageFormat::Vhdx` arm of the create
operation keeps refusing a backing file. The proof that this phase
worked is instar's own phase 3 parser — `vhdx::parse_parent_locator`
(`src/crates/vhdx/src/lib.rs:890`) — reading back what this phase
wrote, with `defect == None`.

## Planning effort

High, as the master plan requires
([PLAN-differencing.md](/components/instar/plans/PLAN-differencing/), "Agent guidance").
Unlike phase 5, almost every byte offset here was already pinned
by measurement in phase 1, so the judgement is not in the field
layout. It is in three places: where the locator item goes without
disturbing the five metadata items already written, which path key
to emit for a POSIX path, and what limit the emitter enforces on a
path length when the format itself sets none.

## Review effort

High, on a narrower question than phase 5's. Phase 5 had no
external oracle at all for the VHD locator table. Phase 6 does
have one — libvhdi reads the VHDX parent locator, and
`vhdiinfo` will report the parent identifier — but it is an oracle
with a known defect (see "The libvhdi `relative_path` trap"
below), so a reviewer must be able to tell a wrong emitter from a
broken reader. Check the emitter against
[PLAN-differencing-phase-01-pin.md](/components/instar/plans/PLAN-differencing-phase-01-pin/),
"VHDX — the parent locator metadata item" and "VHDX — which keys
instar should write", which carry the measured Hyper-V bytes, not
against this plan's restatement of them.

## Scope

In scope:

* `src/crates/vhdx/src/lib.rs` — a builder for the parent locator
  metadata item and its metadata table entry, and a helper that
  renders a 16-byte GUID as the lowercase braced string
  `parent_linkage` requires.
* `src/crates/create/src/lib.rs` — `plan_vhdx` emits a
  differencing child; `VhdxCreateOpts` gains the parent identity
  the planner cannot derive.
* `src/operations/create/src/main.rs` and `src/vmm/src/main.rs` —
  only as far as keeping user-visible behaviour unchanged (see
  decision 6) and keeping the exhaustive `map_create_error` match
  compiling.
* Tests in `src/crates/vhdx/` and `src/crates/create/`.

Out of scope, explicitly:

* **Giving created images a real identity (#566).** Every VHDX
  instar writes shares one `DataWriteGuid` and one Virtual Disk
  ID. Fixing that changes `vhdx::build_header`, which the convert
  operation also calls (`src/operations/convert/src/main.rs:4469`,
  `:4492`), so it is a change to every VHDX instar has ever
  produced and needs its own phase-worth of tests. See decision 7.
* The guest create op actually accepting `-b` for vhdx. Phase 7.
* Reading or composing a differencing VHDX. Phases 11 to 16.
* `docs/create.md`. Its statement that vhdx rejects `backing_file`
  stays true through this phase; phase 10 owns the change.

## What the survey found

The master plan's phase 6 paragraph
([PLAN-differencing.md](/components/instar/plans/PLAN-differencing/), "Execution") is
substantially correct — every structural claim it makes holds —
but four of its five source references have moved, because phase 5
inserted ~200 lines into `src/crates/create/src/lib.rs`. They have
been corrected in the master plan as part of this planning commit,
so a later step should not redo it.

| Claim in the master plan | Cited | Actually |
|---|---|---|
| `build_header` derives the DataWriteGuid from the sequence number | `vhdx/src/lib.rs:2201-2212` | function at `:2201`, the two GUID writes at `:2206-2215` — correct in substance |
| `plan_vhdx` passes sequence numbers 1 and 2 | `create/src/lib.rs:965`, `:967` | `:1161`, `:1163` |
| the convert op does the same | `convert/src/main.rs:4469`, `:4492` | unchanged, still correct |
| `plan_vhd` writes `UUID_ZERO` as every footer's unique id | `create/src/lib.rs:776`, `:820` | `:958`, `:1021` and `:1072` — **three** sites now, not two, and a fourth call site at `:977` writes `opts.parent_unique_id` instead, which is phase 5's differencing path |

[PLAN-differencing-phase-01-pin.md](/components/instar/plans/PLAN-differencing-phase-01-pin/)
has the same drift in the other direction — it cites
`vhdx/src/lib.rs:1479`, `:1420-1504` and `:1341-1345` for
`build_metadata` and `build_header`, which are now `:2273`,
`:2273-2380` and `:2206-2215`. That file is the record of a
completed phase and has been left alone; the corrected pointers
are in this plan's step briefs instead.

Five findings that change what this phase does:

1. **`build_metadata` already takes `has_parent`, and already
   writes the right bit.** `src/crates/vhdx/src/lib.rs:2273`,
   parameter `has_parent: bool`, writing File Parameters flags
   `0x00000002` when set. `plan_vhdx` passes `false` today
   (`src/crates/create/src/lib.rs:1175-1182`). So the `HasParent`
   half of this phase is a one-argument change, exactly as the pin
   predicted.

2. **`plan_vhdx` writes no BAT at all, and that is already the
   correct differencing encoding.** It computes `bat_off` and
   `bat_region_size` (`:1132`, `:1130`) but pushes no
   `MetadataWrite` for them, leaving the BAT region a sparse hole
   of zeros — every payload entry `PAYLOAD_BLOCK_NOT_PRESENT` (0,
   `vhdx/src/lib.rs:184`) and every sector-bitmap entry
   `SB_BLOCK_NOT_PRESENT` (0). The pin quotes SPEC(VHDX) 2.5.1.1
   making that exactly what a differencing child should say, and
   2.5.1.2 permitting the sector-bitmap side because a fresh child
   has no partially-present block. **This phase therefore changes
   nothing about the BAT**, which is a much smaller change than
   phase 5's (the VHD BAT had to be filled with `0xFF`). A test
   should pin it, because "we did nothing and it is correct" is
   indistinguishable from "we forgot" in a diff.

3. **`0x10028` is where instar's own metadata items already end.**
   `build_metadata` puts item data at `items_base = 0x10000` and
   the five existing items consume 8 + 8 + 4 + 4 + 16 = 40 bytes,
   so the next free byte is `0x10028` — the offset Hyper-V's
   measured `fat-differential.vhdx` uses for its parent locator.
   The emitter does not have to reproduce a magic number; it has
   to append.

4. **`PARENT_LOCATOR_GUID` is private.** `src/crates/vhdx/src/lib.rs:178`
   declares it `const`, not `pub const`, and it is used only by
   `parse_metadata` (`:1083`). The pin refers to it as though a
   caller could use it. Both the builder and its test need it, and
   both live in the `vhdx` crate or its tests — but the round-trip
   test in `crates/create` needs it too, so step 6b makes it
   `pub`.

5. **Four doc comments in the `vhdx` crate already describe an
   emitter that does not exist.** `:410-415` says "instar writes
   two" entries, `:462` says instar's locator item "will be
   smaller" than Hyper-V's 674 bytes, `:477` says `build_metadata`
   "would" place an item at `0x10028`, and `:491` says instar
   "never writes" `volume_path`. These were written by phase 3
   against the phase 1 pin, and this phase is what makes them
   true. They are not corrected here — they are a specification of
   what 6b must build, and step 6e re-reads them against the
   shipped emitter.

Nothing else in the phase 6 section was wrong. The `parent_linkage`
= parent `DataWriteGuid` rule, the lowercase-braced rendering, the
`0x00000004` (IsRequired only) metadata entry flags, the 20-byte
locator header and 12-byte entries with item-relative offsets, and
the UTF-16LE no-NUL string encoding are all pinned by measurement
in phase 1 and all still match the tree's parser.

## Decisions

**1. The builder lives in `crates/vhdx` and appends to an
already-built metadata region.** Signature:

```rust
pub fn build_parent_locator(
    metadata: &mut [u8],
    parent_data_write_guid: &[u8; 16],
    path_key: &[u8],
    path_value: &str,
) -> Result<usize, VhdxBuildError>;
```

It bumps the metadata table's entry count from 5 to 6, writes
entry index 5 at offset `32 + 5 * METADATA_TABLE_ENTRY_SIZE`
(`vhdx/src/lib.rs:143`) = `0xC0`, and writes the item body at
`0x10028`. `build_metadata` is **not** modified, so a
non-differencing VHDX is byte-identical to today and the diff
shows that — the same shape phase 5 used for
`build_dynamic_header_parent`. Rejected alternative: a
`build_metadata` variant taking an `Option<locator>`, which would
put a branch through the path every non-differencing image takes.

**2. `parent_linkage` is rendered by the crate, from raw bytes
supplied by the caller.** `VhdxCreateOpts` gains
`parent_data_write_guid: [u8; 16]`, mirroring phase 5's
`parent_unique_id` (`create/src/lib.rs:289` region). The crate
renders it as the lowercase braced `bytes_le` string — 38
characters, e.g. `{f88d4d92-6fcc-408d-9bef-9b7c89f15c89}` —
because that rendering is a property of the format, not of the
caller, and a caller that formatted it would be a second place for
the mixed-endian `bytes_le` order to be got wrong. Note the
`vhdx` crate is `no_std` with no allocator: render into a
`[u8; 38]` on the stack.

**3. Write `parent_linkage` and exactly one path key —
`relative_path` for a relative path, `absolute_win32_path` for an
absolute one.** This is phase 1's pinned decision
([pin](/components/instar/plans/PLAN-differencing-phase-01-pin/), "VHDX — which keys
instar should write") and it is kept. Not `volume_path` (needs a
Windows volume GUID no Linux producer can obtain) and not
`parent_linkage2` (the spec forbids it in the same paragraph that
Hyper-V violates).

**This is the decision most likely to be argued with**, for the
same reason #570 is open against phase 5: instar will write a
POSIX path such as `/srv/images/parent.vhdx` under a key literally
named `absolute_win32_path`. The argument for doing it anyway is
that the alternative is worse in every direction — omitting the
path key entirely violates SPEC(VHDX) 2.6.2.6.3's "at least one
entry with key value of `relative_path`, `volume_path`, or
`absolute_win32_path`", and refusing absolute paths would make
`create -b /abs/path` work for qcow2 and vmdk but not vhdx.
**This phase does not open a second issue**: #570 already frames
the question as "POSIX paths under Windows-only platform codes"
and step 6e extends it to cover the VHDX key rather than filing a
near-duplicate. Phase 7 must settle #570 before either guard comes
off.

**4. The emitter refuses a path longer than 260 UTF-16 code units,
and reuses `CreateError::ParentNameTooLong`.** SPEC(VHDX) sets no
path limit — `ValueLength` is a u16, so the format permits 32767
code units. But instar's own parser stops decoding at
`MAX_PARENT_LOCATOR_VALUE_UTF16 = 520` bytes = 260 code units
(`vhdx/src/lib.rs:439`) and marks a longer value `ValueTooLong`.
Emitting something this crate's own parser flags as defective
would break the invariant phase 5 established — *this crate never
writes an image its own parser calls defective* — so the cap is
the parser's, enforced inside the builder, not re-derived by the
caller.

Reusing phase 5's error code rather than appending a thirteenth is
deliberate: `CreateResult::ERROR_PARENT_NAME_TOO_LONG = 12` is
append-only ABI duplicated across three files with no compile-time
cross-check, and "the parent path does not fit the target format's
parent name field" is one condition with two limits, not two
conditions. **The host message in `src/vmm/src/main.rs` must be
reworded**, because today it names only VHD's 255 and would be a
false diagnostic for a 300-code-unit VHDX path. See step 6c.

**5. One metadata item, two entries, keys before values, no
padding.** Item layout, all offsets relative to the item start:

| Offset | Bytes | Content |
|---|---|---|
| +0 | 16 | locator type GUID `B04AEFB7-D19E-4A81-B789-25B8E9445913`, `bytes_le` |
| +16 | 2 | reserved, `0` |
| +18 | 2 | `KeyValueCount` = 2 |
| +20 | 12 | entry 0 — `parent_linkage` |
| +32 | 12 | entry 1 — the path key |
| +44 | 28 | `parent_linkage` key, UTF-16LE |
| +72 | *k* | path key, UTF-16LE |
| +72+*k* | 76 | linkage value, UTF-16LE, 38 chars |
| +148+*k* | *v* | path value, UTF-16LE |

Total `148 + k + v`, which for the longest legal path
(`absolute_win32_path`, 19 chars = 38 bytes, and a 260-code-unit
value = 520 bytes) is 706 bytes — under `MAX_PARENT_LOCATOR_ITEM`
(4096, `vhdx/src/lib.rs:465`) with room, and far under the 1 MiB
metadata region. Hyper-V interleaves its keys and values; the spec
fixes no ordering ("there is no ordering to the entries") and
grouping them is easier to assert against.

**6. Behaviour visible to a user does not change in this phase.**
`vhdx_opts_from` (`operations/create/src/main.rs:339`) passes a
zero GUID, and the `ImageFormat::Vhdx` arm at `:707` gains an
explicit refusal of a backing file *before* it calls `plan_vhdx` —
because once `plan_vhdx` stops returning `BackingFileUnsupported`,
nothing else stands between a `-b` and a child claiming a parent
whose identity is sixteen zero bytes. This mirrors what phase 5
did for `ImageFormat::Vhd` (`:690-696`) and carries the same
liability: the guard is in the guest binary, `crates/create`'s
harness cannot reach it, so it has **no committed test** until
phase 8 adds an integration test. That debt is already recorded in
the master plan for phase 8 and step 6e extends the note to cover
vhdx.

**7. #566 is not fixed here, and this phase measures it rather
than assuming it.** The master plan asserts from code reading that
every instar VHDX carries active-header `DataWriteGuid`
`{00000002-0000-0000-0200-000000000000}`. Step 6a confirms that
against a real instar-produced file, because the claim is the
premise of phase 8's "the negative identity test must use a
third-party parent" and a premise that has only ever been read,
never measured, is exactly what this repository's history says
goes wrong. Fixing it is out of scope (see Scope).

**8. `MAX_PARENT_LOCATOR_ENTRIES`, `MAX_PARENT_LOCATOR_KEY_UTF16`
and `MAX_PARENT_LOCATOR_ITEM` are not touched.** They are parser
resource bounds and the emitter's output sits well inside all
three. Step 6e verifies that by measurement on emitted bytes, not
by arithmetic in a comment.

## The libvhdi `relative_path` trap

Recorded here because an implementer who does not know it will
"fix" a correct emitter. libvhdi looks the path keys up in the
order `absolute_win32_path`, `volume_path`, `relative_path`
(`libvhdi_metadata_values.c:249`, `:269`, `:290`) — the reverse of
the spec's order — and looks `relative_path` up with a key length
of 12 for a 13-character key. **A differencing VHDX carrying only
`relative_path` therefore produces no "Parent filename" line from
`vhdiinfo` at all.** That is the oracle being broken, measured in
phase 1 step 1a by rewriting a child's locator one key at a time.
It does not affect `parent_linkage`, so `vhdiinfo` will still
print the parent identifier for such a child, and it does not
affect composition through `pyvhdi.file.set_parent()`, which is
how phase 15's harness will work.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | medium | sonnet | none | Measure, do not assume. Build instar (`make instar`; everything Rust builds in Docker in this repo, never on the host) and create a plain dynamic VHDX: `src/target/release/instar create -f vhdx /tmp/probe.vhdx 64M`. Then dump both headers — header 1 at file offset `0x10000` and header 2 at `0x20000` (`vhdx::HEADER1_OFFSET`, `HEADER2_OFFSET`) — and decode, for each, the sequence number (LE u64 at +8, `vhdx/src/lib.rs:115`) and the `DataWriteGuid` (16 bytes at +32, `:119`). The active header is the one with the higher sequence number that passes its CRC-32C (`vhdx/src/lib.rs:741-751` picks it the same way). Render its GUID in `bytes_le` form — first three groups little-endian, last two big-endian — and state whether it equals `00000002-0000-0000-0200-000000000000`, which is what `build_header` (`:2201`, GUID writes at `:2206-2215`) predicts for sequence number 2. Also decode the Virtual Disk ID metadata item (16 bytes at metadata region + `0x10018`) and say whether two images created at different sizes share it. Output is a comment on issue #566 recording the measurement with the raw `xxd` lines, plus a one-line confirmation or correction in this plan under "What the survey found". Change no source. |
| 6b | high | opus | worktree | Add parent-locator emitting to `src/crates/vhdx/src/lib.rs`. (i) Make `PARENT_LOCATOR_GUID` (`:178`) `pub` — survey finding 4; it is currently private and both the builder's test and the round-trip test in `crates/create` need it. (ii) Add `VhdxBuildError { BufferTooSmall, PathTooLong, PathEmpty, UnknownKey }`, mirroring `vhd::VhdBuildError` in shape. (iii) Add a GUID renderer producing the lowercase braced `bytes_le` string into a `[u8; 38]`: `{xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx}` where the first three groups are little-endian and the last two big-endian, per the pin's worked example — parent header bytes `92 4d 8d f8 cc 6f 8d 40 9b ef 9b 7c 89 f1 5c 89` render as `{f88d4d92-6fcc-408d-9bef-9b7c89f15c89}`. The crate is `no_std` with no allocator; no `format!`. (iv) Add `build_parent_locator(metadata: &mut [u8], parent_data_write_guid: &[u8; 16], path_key: &[u8], path_value: &str) -> Result<usize, VhdxBuildError>` per decision 1: rewrite the metadata table entry count (LE u16 at offset 10) from 5 to 6, write table entry index 5 at `32 + 5 * METADATA_TABLE_ENTRY_SIZE` (`:143`) = `0xC0` — ItemID = the parent locator GUID, Offset = `0x10028` (LE u32), Length = the item's exact byte length (LE u32), flags = `0x00000004` (IsRequired only, MEASURED from Hyper-V), Reserved2 = 0 — and the item body at `0x10028` per decision 5's table. Strings are UTF-16 **little** endian with **no** NUL terminator and no internal NUL (SPEC(VHDX) 2.6.2.6.2); use `shared::utf8_to_utf16(s, false, dst)`, which phase 5 added for exactly this. Accept only `KEY_RELATIVE_PATH` (`:489`) or `KEY_ABSOLUTE_WIN32_PATH` (`:495`) as `path_key`; anything else is `UnknownKey`. Refuse an empty path (`PathEmpty`) and a path whose UTF-16 encoding exceeds `MAX_PARENT_LOCATOR_VALUE_UTF16` (`:439`, 520 bytes = 260 code units) with `PathTooLong` — decision 4; enforce it here, not in the caller. Unit tests: assert every field lands at the offset decision 5's table gives by checking raw bytes; assert the rendered linkage string against the pin's worked example; assert `parse_parent_locator` (`:890`) reads the emitted item back with `defect == None`, `parent_linkage()` (`:740`) equal to the rendered string and `preferred_path()` (`:792`) equal to the path; assert a 260-code-unit path succeeds and 261 is refused; assert a non-BMP character costs two code units against that limit. |
| 6c | high | opus | worktree | Teach `plan_vhdx` (`src/crates/create/src/lib.rs:1102`) to emit a differencing child. Delete the early `BackingFileUnsupported` return at `:1115-1118` (whose comment "deferred — too complex for phase 1" is now three phases stale) and take the parent path from `opts.backing`. Add `parent_data_write_guid: [u8; 16]` to `VhdxCreateOpts` (`:324`), following how phase 5 added `parent_unique_id` to `VhdCreateOpts` (`:289`). Pass `has_parent = true` to `build_metadata` (`:1175`) when there is a backing file — survey finding 1, that is the whole `HasParent` change — then call 6b's `build_parent_locator` on the same `metadata_region` slice. Choose the key from the typed path bytes exactly as phase 5 chooses its platform code: `KEY_RELATIVE_PATH` unless the path starts with `/`, in which case `KEY_ABSOLUTE_WIN32_PATH` (decision 3). **Do not touch the BAT** — survey finding 2, the sparse hole is already the correct differencing encoding, and `plan_vhdx` pushes no `MetadataWrite` for the BAT region today. Map `VhdxBuildError` onto `CreateError` with a `map_vhdx_build_error` beside `map_vhd_build_error` (`:490`): `PathTooLong` → `ParentNameTooLong` (decision 4, no new ABI code), `PathEmpty` and `UnknownKey` → `BackingFileUnsupported`, `BufferTooSmall` → `ScratchTooSmall`. Reword the `ERROR_PARENT_NAME_TOO_LONG` host message in `src/vmm/src/main.rs` so it no longer says "VHD allows at most 255 UTF-16 code units" as though that were the only limit — it must name both the VHD field's 255 and the VHDX value's 260 and still say that a non-BMP character costs two. Adding a field to `VhdxCreateOpts` breaks every struct-literal site: `src/operations/create/src/main.rs:339` (`vhdx_opts_from`, which passes a zero GUID per decision 6) and `src/fuzz/fuzz_targets/fuzz_create_emitters.rs:194`, plus the unit tests in `crates/create`. Per decision 6, add an explicit backing refusal to the `ImageFormat::Vhdx` arm at `operations/create/src/main.rs:707`, before `plan_vhdx` is called, mirroring the VHD guard at `:690-696`; comment it with the condition that must become true before it is removed — the guest being able to read the parent's active-header DataWriteGuid — and **not** with a phase number (AGENTS.md, "Plan phase numbers belong in plan documents only"). `debug_assert_eq!(plan.minimum_file_size, total_file_size)` at the end of the function must still hold: the locator lives inside the existing 1 MiB metadata region, so `total_file_size` does not change. |
| 6d | high | opus | worktree | Tests, in `src/crates/create/tests/round_trip.rs` beside the existing `sweep_vhdx_dynamic` (`:332`) rather than a new harness. The load-bearing one is emit-then-parse: build a differencing plan, lay its writes into a byte buffer, slice the metadata region, walk the metadata table by hand (32-byte header, then 32-byte entries; entry count is the LE u16 at offset 10) to find the entry whose ItemID is `vhdx::PARENT_LOCATOR_GUID`, and hand its item bytes to `vhdx::parse_parent_locator`. Assert: the table now has 6 entries; the locator entry's Offset is `0x10028` and its flags `0x00000004`; the item parses with `defect == None`; `parent_linkage()` equals the braced rendering of the opts GUID; `preferred_path()` equals the input path; and File Parameters flags at metadata + `0x10004` are `0x00000002`. Add a **negative control** so the positive assertions are demonstrably not vacuous: place the item at `0x10000` instead of `0x10028` — overlapping the five existing items — and assert the result is detected, either by the table walk finding overlapping ranges or by `parse_parent_locator` reporting a defect; if neither fires, say so in the test's comment, because that is a real gap in the parser rather than something to paper over. Also assert: a relative path yields `relative_path` and an absolute one `absolute_win32_path`; a 261-code-unit path surfaces as `CreateError::ParentNameTooLong` through `plan_vhdx` (the boundary itself is tested in 6b — what 6d adds is that `plan_vhdx` surfaces it rather than re-deriving it); **the BAT region is still entirely zero and the plan still pushes no write for it** (survey finding 2); and a **byte-for-byte regression test that a non-differencing VHDX is unchanged**, comparing against the bytes `plan_vhdx` produces on `develop` at `9a80776` for the same options, in the style of phase 5's `GOLDEN_VHD_NO_BACKING`. Update `plan_vhdx_rejects_backing` (`src/crates/create/src/lib.rs:1869`), which this phase makes wrong: it should now assert that `VhdxCreateOpts` with a backing *succeeds*, and that the refusal has moved to the operation. |
| 6e | medium | sonnet | none | Closeout. (i) Build and briefly run `fuzz_create_emitters` (`make fuzz-build`, then `make fuzz-run FUZZ_TARGET=fuzz_create_emitters FUZZ_DURATION=60`) — it needs no code change beyond 6c's struct literal, but it now reaches a `plan_vhdx` path it has never reached, and phase 5's invariant 5 (no two writes overlap) is exactly the oracle a mis-sized locator item would trip. (ii) Re-read the four forward-looking doc comments in `src/crates/vhdx/src/lib.rs` (`:410-415`, `:462`, `:477`, `:491`) against what 6b and 6c actually shipped and correct any that are now wrong — "instar writes two" in particular is a claim this phase either makes true or falsifies. (iii) Add a `CHANGELOG.md` entry for the crate-level capability, phrased so it promises no user-facing feature: `create -f vhdx -b` still refuses. Link the master plan, not a phase number. (iv) Add a comment to #570 extending it to the VHDX `absolute_win32_path` key (decision 3) — do not open a new issue. (v) Confirm #566 is still open and that 6a's measurement is recorded on it. (vi) Do **not** touch `docs/create.md`. |

## Risks and mitigations

* **A wrong `bytes_le` rendering passes every instar test.**
  instar's own parser compares `parent_linkage` as an opaque byte
  string (`linkage_matches`, `vhdx/src/lib.rs:805`), so an emitter
  that renders the GUID in the wrong group order round-trips
  through instar perfectly and fails only against Hyper-V or
  libvhdi. *Mitigation:* 6b asserts the rendering against the
  pin's measured worked example — parent header bytes to child
  string — not against instar's own output. The reviewer checks
  that specific test.

* **The metadata table entry count is rewritten in place, and a
  half-applied write leaves a table claiming six items with five
  present.** *Mitigation:* `build_parent_locator` writes the item
  body first and the count last, and returns `BufferTooSmall`
  before writing anything if the region cannot hold the item. 6d's
  round-trip reads the count back.

* **#570 grows rather than resolves.** Extending an open issue to
  a second format is how an issue quietly becomes permanent.
  *Mitigation:* phase 7's plan must resolve #570 before removing
  either the vhd or the vhdx guard; that is recorded in the master
  plan's phase 7 paragraph as part of this commit.

* **The implementer "fixes" a correct emitter because `vhdiinfo`
  prints no parent filename.** *Mitigation:* "The libvhdi
  `relative_path` trap" above, and 6d asserts against instar's own
  parser rather than against `vhdiinfo`.

* **6a is skipped because the answer is obvious.** It is obvious;
  it is also the premise of phase 8's test design and has never
  been measured. *Mitigation:* 6a's deliverable is a comment on
  #566 with raw `xxd` lines, which is either there or not.

## Definition of done

* `plan_vhdx` with a backing reference returns a plan whose bytes,
  laid into a buffer, yield a metadata table of six entries whose
  sixth is the parent locator at offset `0x10028` with flags
  `0x00000004`, and whose item `vhdx::parse_parent_locator` reads
  back with `defect == None`, `parent_linkage()` equal to the
  braced rendering of the supplied GUID, and `preferred_path()`
  equal to the supplied path.
* File Parameters flags are `0x00000002` for a differencing child
  and `0x00000000` for a non-differencing one, asserted on emitted
  bytes.
* The BAT region of a differencing child is entirely zero and the
  plan contains no write targeting it, asserted by a test rather
  than left implicit.
* The negative control in 6d fails when the item is deliberately
  placed at `0x10000`, or the test records in a comment that
  neither the table walk nor the parser detects it.
* `plan_vhdx` with no backing produces **byte-identical** output
  to `develop` at `9a80776` for the same options, proven by a
  committed test rather than by inspection.
* A 260-UTF-16-code-unit path succeeds and a 261-code-unit one is
  refused as `CreateError::ParentNameTooLong`; a non-BMP character
  costs two code units against that limit.
* The rendered `parent_linkage` for the pin's measured parent
  header bytes is exactly
  `{f88d4d92-6fcc-408d-9bef-9b7c89f15c89}`, asserted by a test.
* The `ERROR_PARENT_NAME_TOO_LONG` host message names both the VHD
  and VHDX limits; no string in `src/vmm/src/main.rs` states a
  parent-path limit that is true for only one of the two formats.
* `make test-rust` passes, `make check-binary-sizes` passes (no
  guest binary grows past 768 KB — 6c adds a guard and a builder
  to the create op's binary, so unlike phase 5 this is a budget
  question, not only a regression check), and
  `pre-commit run --all-files` is clean.
* `fuzz_create_emitters` builds and runs against the new path
  without tripping its `minimum_file_size` or overlap oracles.
* `docs/create.md` is unchanged, and `create -f vhdx -b` still
  fails with the same error a user sees today. **Verified by hand
  against a binary built from this branch**, not by a committed
  test: `create -f vhdx parent.vhdx 64M` succeeds and
  `create -f vhdx -b parent.vhdx -F vhdx child.vhdx 64M` gives
  `Error: "create failed: invalid option for target format"`,
  exit 1, with no child file written. As in phase 5, the guard is
  in the guest binary and `crates/create`'s harness cannot reach
  it; phase 8 owns the integration test that pins it.
* Issue #566 carries 6a's measurement, and #570 carries the VHDX
  key extension. Neither is closed by this phase.

## Back brief

Before any code moves, the implementing session states back:

1. Which key it will write for the path `../parent.vhdx`, and
   which for `/srv/images/parent.vhdx`, and why the second one is
   the subject of #570.
2. The exact byte offset within the metadata region at which the
   locator item begins, and the arithmetic that produces it from
   `build_metadata`'s existing five items.
3. What it expects to change about the BAT.

Answer 3 is "nothing", and an implementer who answers otherwise
has not read survey finding 2.

**A gate before 6c.** 6b's `build_parent_locator` signature is
cheap to propose and expensive to redo once `plan_vhdx`, the
fuzz target, the create op and three test modules call it.
The management session reviews the shipped signature and the
metadata-table mutation it performs before 6c starts.
