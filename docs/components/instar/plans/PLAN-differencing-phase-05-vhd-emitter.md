# PLAN: Differencing phase 5 — the `plan_vhd` differencing emitter

Phase 5 of [PLAN-differencing.md](/components/instar/plans/PLAN-differencing/).

## Goal

Make `create::plan_vhd` able to emit a differencing VHD: footer
`disk_type = 4`, a dynamic header carrying the parent's unique id,
timestamp and unicode name, and exactly one populated parent
locator entry with its platform data written into the file.

This phase is the emitter only. It changes a crate function and
the format crate underneath it; it does not make
`instar create -f vpc -b parent.vhd` work, because the guest has
nowhere to get the parent's identity from until phase 7 reads the
parent's footer. The proof that this phase worked is instar's own
phase 3 parser reading back what this phase wrote.

## Planning effort

High, as the master plan requires for this phase. The judgement is
not in any single field — it is in the layout (the locator data
region is new file real estate, and the parser already constrains
where it may sit) and in two encoding traps the format sets, both
of which are pinned by measurement and neither of which any
external tool will catch for us.

## Review effort

High, concentrated on one question: **is every emitted byte at the
offset and in the endianness the format actually uses?** The
master plan's standing warning applies at full force here —
libvhdi never parses the VHD locator table at all, and qemu-img
reads only the parent unicode name, so a wrong locator is
invisible to both oracles. A reviewer should check the emitter
against `SPEC(VHD)` and the phase 1 pin directly, not against this
plan's restatement of them.

## Scope

In scope:

* `src/crates/vhd/src/lib.rs` — a parent-aware dynamic header
  builder, a locator entry builder, and a UTF-8 → UTF-16 encoder
  that is the inverse of the existing `shared::utf16_to_utf8`.
* `src/crates/vhd/src/lib.rs` — expose the footer timestamp, which
  the struct does not currently carry.
* `src/crates/create/src/lib.rs` — `plan_vhd` accepts a backing
  reference, gains the parent-identity inputs it cannot derive,
  and grows a locator data region in its layout.
* Rust unit and round-trip tests, including emit-then-parse
  against the phase 3 parser.

Out of scope, and each named with the phase that owns it:

* **VHDX** — phase 6. The two emitters are independent; this one
  goes first because the VHD locator table is the simpler
  structure.
* **The guest create op and the host CLI** — phase 7. `-b` on a
  vpc target keeps returning `BackingFileUnsupported` to the user
  until phase 7 lands. Two small mechanical changes to
  `src/operations/create` and one to `src/vmm` are unavoidable and
  are in scope: the `VhdCreateOpts` literal, the explicit guard of
  decision 8, and the new error code's `map_create_error` arm and
  host message. None of them changes what a user sees. That is a deliberate intermediate state:
  the emitter is reviewable on its own, and wiring it before it is
  reviewed would mean shipping a format writer whose output
  nothing had read back.
* **Python integration tests** — phase 8. This phase's tests are
  Rust, in-crate, and do not need a VM.
* **Coverage fuzzing** — phase 9, with one exception noted under
  risks: `fuzz_create_emitters` already feeds a `backing` into
  `plan_vhd`, so this phase changes what that target reaches
  whether it wants to or not.
* **User-facing documentation** — phase 10 owns the cross-cutting
  pages. `docs/create.md`'s current statement that vpc rejects
  `-b` stays true through this phase and must not be edited.
* **Composition on read** — phases 11 to 16. Anything this phase
  writes is still refused by phase 4's read-side policy, which is
  correct and expected.
* **Giving created VHDs a real footer UUID** — see decision 2,
  filed as #566.

## What the survey found

The master plan's phase 5 claims were checked against the tree at
`f981374`. **No claim of substance was wrong.** Every fact the
plan asserts about `plan_vhd` still holds. What had rotted was
navigation: nine of the file:line references the master plan
carries were moved by phases 3 and 4, which between them added
roughly 1,500 lines to `crates/vhd` and `crates/vhdx` and
rewrote parts of `src/vmm/src/main.rs`. Those are corrected at
source in the same commit as this plan, so this section records
the check rather than the fix:

| Master plan said | Actually at |
|------------------|-------------|
| `crates/vhdx/src/lib.rs:1341-1345` (`build_header` DataWriteGuid) | `:2201-2212` |
| `crates/create/src/lib.rs:964-966` (`plan_vhdx` sequence numbers) | `:965`, `:967` |
| `crates/vhd/src/lib.rs:664-673`, `:766` (bitmap skip) | `:1432-1441`, `:1534` |
| `crates/vhdx/src/lib.rs:654` (sector bitmap skip) | `:1820` |
| `crates/vhdx/src/lib.rs:571`, `:581`, `:601` (`PARTIALLY_PRESENT`) | `:1408`, `:1438`, `:1487` |
| `vmm/src/main.rs:16612` (`run_create_nonraw`) | `:16815` |
| `vmm/src/main.rs:16710-16769` (`typed_backing`) | `:16913-16963` |
| `vmm/src/main.rs:2416` (`discover_backing_chain`) | `:2501` |

Verified unchanged and still correct: `crates/create/src/lib.rs:776`
and `:820` (`UUID_ZERO`), `operations/convert/src/main.rs:4469`
and `:4492` (sequence numbers 1 and 2), `vmm/src/config.rs:65`
and `:67` (the two security settings).

Five findings that are new, and that shape the step plan:

1. **`VhdFooter` has no `timestamp` field.** The master plan says
   the guest can read the parent's footer "for its unique id and
   timestamp" without a new call-table primitive, and that is
   true — but `VhdFooter` (`crates/vhd/src/lib.rs:182`) exposes
   `uuid` and not the timestamp at `FOOTER_TIMESTAMP_OFFSET`
   (`:48`). Phase 7 would have to reach past the parser to get it.
   Adding the field belongs here, with the rest of this phase's
   `crates/vhd` work.

2. **There is no UTF-8 → UTF-16 encoder anywhere in the tree.**
   `shared::utf16_to_utf8` (`src/shared/src/lib.rs:187`) exists
   and is thoroughly tested in both endiannesses, including
   surrogate pairs and the refusal cases; nothing goes the other
   way. The emitter needs one, in `no_std`, panic-free, for two
   different endiannesses (see decision 3).

3. **`MAX_BACKING_FILE_LEN` is 1024 bytes and the destination
   field is 512.** `crates/create/src/lib.rs:24` caps a backing
   path at 1024 bytes for every format; the VHD parent unicode
   name field is `DYN_PARENT_NAME_SIZE = 512` bytes, which is 256
   UTF-16 code units (`crates/vhd/src/lib.rs:117`). An ASCII path
   of 300 bytes passes the generic check and does not fit. The VHD
   path needs its own, tighter limit and must fail loudly rather
   than truncate — a truncated parent name is a path to a
   different file.

4. **The parser already constrains where the locator data may
   sit.** `locator_defect` (`crates/vhd/src/lib.rs`, reached from
   `VhdParentLocator::parse:507`) rejects platform data that
   overlaps the head footer (`0..512`), overlaps the dynamic
   header (`header_offset..header_offset+1024`), runs past
   `image_len`, or has `platform_data_length > platform_data_space`.
   Decision 1's layout is the one that satisfies all four, and
   emit-then-parse is therefore a real check rather than a
   tautology.

5. **`fuzz_create_emitters` already passes a `backing` to
   `plan_vhd`** (`src/fuzz/fuzz_targets/fuzz_create_emitters.rs:179-181`).
   Today it can only ever get `BackingFileUnsupported` back. The
   moment `plan_vhd` accepts a backing, the fuzzer starts driving
   the new layout against its existing oracles —
   `plan.minimum_file_size` and the non-overlapping-writes
   assertion. That is free coverage and a free trap: those
   oracles must hold for the new region too.

One more, recorded because it is the reason a later phase's test
cannot be written the obvious way. `plan_vhd` writes `UUID_ZERO`
as the footer unique id of every image it creates
(`crates/create/src/lib.rs:776`, used at `:820`, `:841`, `:875`).
A differencing child copies its parent's unique id into the
dynamic header, so an instar-created parent gives an all-zero
`parent_unique_id`, and **any instar parent satisfies any instar
child**. The master plan flags the equivalent VHDX hole for
phase 6; the VHD half is this phase's to notice and is handled by
decision 2.

## Decisions

**1. The locator data region goes between the dynamic header and
the BAT, one sector wide, at a fixed offset of 1536.**

The layout `plan_vhd` emits today is footer copy at 0, dynamic
header at 512, BAT at 1536, tail footer after the BAT. The locator
data has to go somewhere, and there are three candidates: before
the BAT, after the BAT, or inside the 1024-byte dynamic header's
unused tail. The last is wrong — the header is a fixed 1024 bytes
and `locator_defect` explicitly refuses data overlapping it.
Between header and BAT is chosen over after-the-BAT for two
reasons. It keeps every metadata structure in a contiguous prefix,
so `minimum_file_size` arithmetic stays a running sum with no
hole; and it matches what both measured Hyper-V images do, which
costs nothing to match and is one less way to look unusual to a
reader we cannot test against.

One sector, not more. The maximum platform data this phase can
emit is a 256-code-unit path, 512 bytes of UTF-16 — exactly one
sector. `platform_data_space` is therefore 512 and
`platform_data_length` is the actual encoded byte count, which
satisfies the parser's `length <= space` rule with the slack in
the right direction. The BAT moves from 1536 to 2048 for a
differencing child and stays at 1536 for a non-differencing one;
the implementer must not "simplify" this by moving the BAT
unconditionally, because that would change the bytes of every
non-differencing VHD instar writes and break golden comparisons
that have nothing to do with this plan.

**2. This phase does not give created VHDs a real footer UUID.
Filed as #566 instead, with the consequence recorded.**

This is the decision most likely to be argued with, so the
reasoning is set out in full. The all-zero unique id makes the
parent-identity check vacuous for chains instar wrote end to end,
and it is tempting to fix it here, in the emitter, where it is
visible. Three things argue against.

It is not a differencing defect. Every VHD instar has ever
created has a zero unique id, dynamic and fixed alike, and
changing that changes the bytes of every `create -f vpc`
invocation — including the ones `tests/test_create.py` compares
against qemu-img. That is a blast radius belonging to its own
change, not to a rider on an emitter.

There is no entropy in the guest. A grep for a random source
across `src/shared` and `src/crates` returns nothing: the guest
has no RNG, and a real UUID would have to be generated on the
host and passed through the call table — which is precisely the
ABI change the master plan's phase 7 rationale says this plan does
not need. Making that change for a cosmetic field, inside the
phase that is meant to be the simpler of the two emitters, is the
wrong trade.

And it would not buy a test. The master plan already settles this
for VHDX: phase 8's negative identity test "must be built against
a **third-party** parent either way, because an instar-created
parent cannot fail it." A real UUID in `plan_vhd` would make
instar-to-instar chains verifiable, but the test that matters —
does instar reject a child whose parent id disagrees? — is a
phase 11-to-16 read-side question and will use a Hyper-V fixture
regardless.

So: #566 covers both formats and is linked from the master plan's
Future work, and phase 8's brief must say the negative identity
test uses a third-party parent. The emitter writes
whatever `parent_unique_id` the caller hands it and does not
second-guess an all-zero one, because an all-zero id is what a
faithful child of an instar parent has.

**3. The two endiannesses are named at every call site, and the
encoder takes the flag rather than defaulting.**

The VHD format uses UTF-16 **big** endian for the parent unicode
name at header offset 64, and UTF-16 **little** endian for the
`W2ru`/`W2ku` platform data. Phase 3's parser documents this in
two places (`crates/vhd/src/lib.rs:104` and `:588`) precisely
because it is the trap. The encoder this phase adds therefore
takes an explicit `big_endian: bool`, exactly as
`shared::utf16_to_utf8` does, and neither call site is allowed to
rely on a default. A unit test asserts the same path encodes to
two different byte strings, so a future refactor that unifies them
fails rather than silently writing a name no Windows reader can
resolve.

**4. `platform_data_space` is written in bytes, not sectors.**

`SPEC(VHD)`'s wording implies a 512-byte sector count. It is
wrong, and phase 1 pinned it: the sector reading is arithmetically
impossible on both measured Hyper-V images, and phase 3's parser
documents the field as "A **byte** count, not the 512-byte sector
count SPEC(VHD)'s wording implies" (`crates/vhd/src/lib.rs:474`).
The emitter writes 512, meaning 512 bytes. This decision exists as
its own numbered item because writing `1` here is the single most
plausible wrong value in the whole phase, it round-trips through
nothing that would catch it, and no external oracle looks at this
field at all.

**5. A differencing child is Dynamic-only; `Fixed` plus a backing
is rejected.**

`disk_type = 4` and a fixed layout are incompatible — a
differencing disk needs a BAT and a dynamic header to say which
blocks are the child's, and a fixed VHD has neither. Hyper-V
produces no such image and `SPEC(VHD)` describes none. The
`VhdSubformat::Fixed` arm of `plan_vhd` returns
`CreateError::BackingFileUnsupported` for a backing reference,
which is the existing error with its existing meaning: this
target format, in this subformat, does not support backing files.

**6. The over-length parent path gets its own error, and the limit
is 255 UTF-16 code units, not 256.**

**Both halves of this decision reverse what this plan originally
said.** It was written as "reuse `BackingFileTooLong`, cap at 256",
and implementation falsified both. The original reasoning is kept
below each correction so the reversal can be judged rather than
taken on trust.

*The limit is 255.* The plan said 256 succeeds and 257 fails.
Phase 1 pinned 255, with a measured reason this plan did not
account for: a path of exactly 256 code units fills all 512 bytes
with no room for a terminating NUL, and that is precisely the
image that trips libvhdi defect C — the oracle reads past the
field into the locator table and reports a parent filename with a
stray character appended. A 512-byte-inclusive rule would have
instar emitting output the only tool that resolves VHD parents
misreads. `crates/vhd`'s own `DYN_PARENT_NAME_SIZE` doc comment
already recorded the 255 emit-side bound against the 256
parse-side bound; this plan contradicted a constant in the tree it
was planning against. The emitter hands the encoder two bytes less
than the field, so the cap is structural rather than a comparison
that can be got wrong, and the last code unit is always zero.

*It gets its own error.* The plan argued that a new variant costs
an append-only `CreateResult` code and buys nothing a user can act
on differently. That is wrong on the facts: the host renders
`ERROR_BACKING_TOO_LONG` as **"backing file path too long (max
1024 bytes)"** (`src/vmm/src/main.rs:17541`), so reusing it would
tell a user that their 300-byte path exceeded a 1024-byte limit.
That is a false statement in a diagnostic — the same class of
misdiagnosis as #548, argued at length in phase 4's review, and
reusing the code here would have reintroduced it one phase later.
So: a new `CreateError::ParentNameTooLong`, a new appended
`CreateResult::ERROR_PARENT_NAME_TOO_LONG`, a `map_create_error`
arm and a host message that names the real limit.

The check lives in `crates/vhd`'s builder rather than in
`plan_vhd`, because that is where the encoding happens and a
second implementation of the same limit is a second chance to
disagree with it. Counting is done during encoding; it is never
estimated from the UTF-8 length, which is neither 255 nor 510
bytes once any character outside the BMP appears.

**7. Both the parent unicode name and the locator entry are
written, always.**

They are redundant for any single reader and neither is optional.
libvhdi resolves a VHD parent from the unicode name field alone
and never looks at the locator table; qemu's `block/vpc.c` does
the same. Windows uses the locator. Writing only the name would
produce a child Hyper-V cannot open; writing only the locator
would produce one neither of our oracles can open, which would
also make phase 8 untestable. Open question 3 already settled
*which* locator entry — one, in slot 1, `W2ru` for a relative
typed path and `W2ku` for an absolute one, slots 2 to 8 zero — so
this decision only records that the name field is not dropped in
favour of it.

**8. The guest keeps refusing `create -f vpc -b`, by an explicit
guard that phase 7 removes.**

Forced by the same discovery as decision 6's scope correction.
Once `plan_vhd` accepts a backing, the guest create op reaches the
new emitter the moment a user passes `-b` on a vpc target — the
call at `src/operations/create/src/main.rs:668` is already there
and already forwards `backing_ref`. Letting that through would
emit a differencing VHD whose `parent_unique_id` is whatever
`vhd_opts_from` invented, and since the guest cannot read the
parent's footer until phase 7, that means zero.

Zero happens to be right for an instar-created parent, because
every VHD instar writes has an all-zero footer id (#566). It is
wrong for every third-party parent, and a child pointing at a
Hyper-V parent with a zeroed identity is exactly the silently
wrong output this whole plan exists to stop. So `vhd_opts_from`
passes zeros and the guest arm refuses vpc-plus-backing before
calling the planner, with `CreateError::BackingFileUnsupported` —
the error and the message a user gets today, so the phase is
invisible from outside. The guard carries a comment naming phase 7
as the step that removes it.

The alternative — leave the phase's own emitter unreachable by
not adding the fields at all — is not available, because the
fields are what makes the emitter testable.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | medium | sonnet | none | Add a UTF-8 → UTF-16 encoder to `src/shared/src/lib.rs`, beside `utf16_to_utf8` (`:187`), with the mirror-image signature: `pub fn utf8_to_utf16(src: &str, big_endian: bool, dst: &mut [u8]) -> Option<usize>`, returning the number of **bytes** written, `None` if `dst` is too small or the encoding overflows 256 UTF-16 code units is *not* this function's business — length policy belongs to the caller, so return `None` only for a `dst` that cannot hold the result. `src/shared` is `no_std`, panic-free, no allocator: use `char::encode_utf16` via a manual surrogate split rather than anything allocating, and index `dst` with explicit bounds checks. Test it as the inverse of `utf16_to_utf8` over the same cases that function already tests (`:6441-6690`): ASCII in both endiannesses, a surrogate pair, a multi-byte BMP form, and a `dst` exactly one byte too small. Add one round-trip test per case asserting `utf16_to_utf8(utf8_to_utf16(s)) == s`. Touch nothing else. |
| 5b | medium | sonnet | none | Expose the VHD footer timestamp. In `src/crates/vhd/src/lib.rs`, add `pub timestamp: u32` to `VhdFooter` (`:182`) and populate it in `VhdFooter::parse` (`:202`) from `FOOTER_TIMESTAMP_OFFSET` (`:48`, big-endian, seconds since 2000-01-01 00:00:00 UTC). This is additive and every existing construction site is inside `parse`, but build the whole workspace afterwards: `VhdFooter` is `pub` and struct-literal construction elsewhere would break. Add one unit test asserting the field is read from offset 24 and not from a neighbouring field, using a footer whose timestamp differs from its `original_size` and `current_size`. Do not change `build_footer` — the timestamp it writes is the creation time of the image being created, which is a different value from the parent's, and step 5c handles the parent's. |
| 5c | high | opus | worktree | Add parent-emitting support to `src/crates/vhd/src/lib.rs`. (i) A builder for the parent half of the dynamic header. Do not change `build_dynamic_header` (`:1889`) — add a second function that takes an already-built header buffer and fills the parent fields, so the non-differencing path is byte-identical to today and the diff shows that. It writes `parent_unique_id` (16 raw bytes at `DYN_PARENT_UNIQUE_ID_OFFSET`, `:101`), `parent_timestamp` (big-endian u32 at `:103`), and the parent unicode name (UTF-16 **BIG** endian at `DYN_PARENT_NAME_OFFSET`, `:106`, field size `DYN_PARENT_NAME_SIZE` = 512 bytes = 256 code units, `:117`), zero-padded to the full field, using 5a's encoder with `big_endian = true`. (ii) A builder for one locator entry, writing into the 24 bytes at `DYN_PARENT_LOCATORS_OFFSET + slot * PARENT_LOCATOR_ENTRY_SIZE` (`:119`, `:124`): platform code as four ASCII bytes in **file order, not byte-swapped** (`:468`), `platform_data_space` and `platform_data_length` as big-endian u32 at `:131` and `:133`, `reserved` zero at `:135`, `platform_data_offset` as a big-endian u64 absolute file offset at `:137`. **`platform_data_space` is a byte count — see decision 4.** (iii) A helper that encodes the platform data itself: UTF-16 **LITTLE** endian (`:588`, the opposite of the name field), no NUL terminator (`:481`). (iv) Recompute the dynamic header checksum after the parent fields are written — `compute_checksum` (`:995`) with `DYN_CHECKSUM_OFFSET` (`:90`); a header whose checksum predates its parent fields is the defect this step is most likely to ship. Unit tests: assert each field lands at the documented offset by checking the raw bytes; assert the two endiannesses differ for the same path (decision 3); assert the checksum validates after the parent fields are written. |
| 5d | high | opus | worktree | Teach `plan_vhd` (`src/crates/create/src/lib.rs:749`) to emit a differencing child. Replace the `BackingFileUnsupported` early return at `:767` with a split: `VhdSubformat::Fixed` plus a backing still returns it (decision 5); `Dynamic` plus a backing takes the new path. Add to `VhdCreateOpts` (`:280`) the two values the planner cannot derive — `parent_unique_id: [u8; 16]` and `parent_timestamp: u32` — following how `VmdkCreateOpts::parent_cid` (`:258-261`) already carries a parent-derived value into a planner. Layout per decision 1: head footer 0, dynamic header 512, **locator data 1536 (one sector)**, BAT 2048, tail footer after the BAT; the non-differencing layout is unchanged, BAT still at 1536. Both footers get `vhd::DISK_TYPE_DIFFERENCING` (`crates/vhd/src/lib.rs:166`) instead of `DISK_TYPE_DYNAMIC`. Pick the platform code from the typed path bytes: `W2ru` if relative, `W2ku` if it starts with `/` (open question 3 — one entry, slot 1, slots 2 to 8 left zero). The 255-code-unit limit is already enforced inside `crates/vhd`'s builder (step 5c) — do **not** re-implement it here; map its `VhdBuildError::ParentNameTooLong` onto a **new** `CreateError::ParentNameTooLong` (decision 6). Because `map_create_error` (`src/operations/create/src/main.rs:105`) is an exhaustive match, that variant also needs an arm there, a new appended `CreateResult::ERROR_PARENT_NAME_TOO_LONG` beside `ERROR_BACKING_DIFFERENCING` in `src/shared/src/lib.rs`, and a host message in `src/vmm/src/main.rs` beside `:17541` that names the real limit rather than 1024 bytes. Adding fields to `VhdCreateOpts` breaks seven struct-literal sites — update all of them, including `src/operations/create/src/main.rs:310` and `src/fuzz/fuzz_targets/fuzz_create_emitters.rs:175`. Per decision 8, `vhd_opts_from` passes zeros for the parent identity and the guest's `ImageFormat::Vhd` arm refuses backing-plus-vpc **before** calling the planner, with `BackingFileUnsupported`, carrying a comment naming phase 7 as the step that removes it — so user-visible behaviour is unchanged by this phase. The plan gains a fifth `MetadataWrite` for the locator data; `MAX_METADATA_WRITES` is 96 (`:122`) and `VHD_MAX_METADATA_SCRATCH` is 4 MiB (`:46`), so neither is near a limit, but `debug_assert_eq!(plan.minimum_file_size, total_file_size)` at the end of the Dynamic arm must still hold. |
| 5e | high | opus | worktree | Tests, in `src/crates/create/` and `src/crates/vhd/`. The load-bearing one is **emit-then-parse**: build a differencing plan, lay its writes into a byte buffer, and read it back with the phase 3 parser — `VhdFooter::parse`, `VhdDynamicHeader::parse`, and `VhdParentInfo::parse` (`crates/vhd/src/lib.rs:936`) with a `VhdImageBounds { image_len, header_offset: 512 }`. Assert: `disk_type == 4`; the decoded parent name equals the input path; the selected locator is the expected one with `defect == None` (which is what proves the layout satisfies `locator_defect`'s four rules — survey finding 4); `platform_data_space == 512` and `platform_data_length` equal to the encoded byte count. Add a negative-control test that a locator placed at 512 instead of 1536 comes back with `OverlapsHeader`, so the round-trip test is demonstrably not vacuous. Add: a relative path yields `W2ru` and an absolute one `W2ku`; a 256-code-unit path is rejected while a 255-code-unit one succeeds, and a non-BMP character costs two code units against that limit (127 fit, 128 do not) — the boundary tests for this already exist in `crates/vhd` from step 5c, so what 5e adds is that `plan_vhd` surfaces the refusal as `CreateError::ParentNameTooLong` rather than swallowing or re-deriving it; `create -f vpc -b` still fails exactly as it does on develop (decision 8); `VhdSubformat::Fixed` plus a backing still returns `BackingFileUnsupported`; and a **byte-for-byte regression test that a non-differencing dynamic VHD is unchanged**, comparing against the bytes `plan_vhd` produces on `develop` for the same options. Extend `src/crates/create/tests/round_trip.rs` (`:284`, `:310`) rather than starting a new harness, and update `plan_vhd_rejects_backing` (`:1467`), which this phase makes wrong. |
| 5f | medium | sonnet | none | Closeout. (i) Confirm `fuzz_create_emitters` (`src/fuzz/fuzz_targets/fuzz_create_emitters.rs:179-181`) still holds its oracles now that `plan_vhd` accepts a backing — build it and run it briefly against the new path; it needs no code change, but survey finding 5 means it is now reaching code it never reached before, and a `minimum_file_size` that disagrees with the writes will surface here first. (ii) Add a `CHANGELOG.md` entry for the crate-level capability, phrased so it does not promise a user-facing feature: `create -f vpc -b` still returns `BackingFileUnsupported` until phase 7. (iii) Confirm #566 (decision 2) is still open and still accurate against the tree at the end of this phase; it is already linked from the master plan's Future work, so this is a check, not a filing. (iv) Do **not** touch `docs/create.md` — its statement that vpc rejects `-b` is still true and phase 10 owns the change. |

Steps 5a and 5b are independent of each other and of everything
else; both can run first. 5c depends on 5a and 5b. 5d depends on
5c. 5e depends on 5d. 5f last.

## Risks and mitigations

* **A plausible-looking wrong offset or endianness ships, and
  nothing catches it.** The defining risk of this phase: libvhdi
  never parses the VHD locator table, qemu-img reads only the
  parent unicode name, and no Windows host is in reach. A wrong
  `platform_data_space`, a byte-swapped platform code, or the
  name field written little-endian would all produce a file that
  instar reads back perfectly. *Mitigation:* every offset and
  endianness in the 5c and 5d briefs is cited to a line in phase
  3's parser, which was itself written against measured Hyper-V
  bytes; the management session re-reads those lines against the
  source before accepting the step, rather than against this
  plan. The emit-then-parse test in 5e is necessary but not
  sufficient — it shares its assumptions with the emitter — which
  is why 5e also carries a negative control.

* **The non-differencing path changes by accident.** Moving the
  BAT is a one-character difference between "only for a
  differencing child" and "always", and the second breaks every
  golden comparison in `tests/test_create.py` for reasons no one
  reading the failure would connect to this plan. *Mitigation:*
  5e's byte-for-byte regression test against `develop`'s output,
  and 5c's instruction to add a second header builder rather than
  modify `build_dynamic_header`, so the diff itself shows the
  old path untouched.

* **The parent name is silently truncated.** 1024 bytes pass the
  generic check and 512 bytes fit the field (survey finding 3). A
  truncated path names a different file, which is worse than a
  refusal in exactly the way phase 4 spent its whole review
  arguing. *Mitigation:* decision 6 puts the check in `plan_vhd`
  on the encoded code-unit count, and 5e tests the boundary at
  256 and 257 code units and with a non-BMP character.

* **The dynamic header checksum is computed before the parent
  fields are written.** Mechanically easy to get wrong, and
  invisible to instar's own reader: `VhdDynamicHeader::parse`
  validates the `cxsparse` cookie and **not** the checksum, as
  phase 4's review established. qemu-img validates the *footer*
  checksum only. *Mitigation:* 5c orders the checksum last and
  tests it explicitly; 5e's round-trip cannot catch this, and the
  brief says so.

* **The fuzz target starts failing on unrelated PRs.** 5f runs it
  deliberately rather than waiting for the nightly to find it.

  *Found in review:* this risk's mitigation, and survey finding 5,
  both claimed `fuzz_create_emitters` carries a
  non-overlapping-writes oracle. **It did not** — `assert_invariants`
  checked bookkeeping, write containment, overflow and the write
  count, and the overlap check lived only in
  `crates/create/tests/round_trip.rs::assert_plan_invariants`. The
  oracle has been added to the fuzz target rather than the claim
  softened, because it now applies to all four emitters and the
  differencing layout is exactly the kind of change that breaks it.

* **The locator entry says "Windows" and holds a POSIX path.**
  *Found in review, deliberately not fixed here — issue #570.* The
  platform code is chosen from the typed path's first byte, so
  `/srv/images/parent.vhd` is emitted as `W2ku`, which SPEC(VHD)
  defines as an absolute Unicode pathname *on Windows*; measured
  Hyper-V output writes `.\fat-parent.vhd` and `C:\Projects\...`
  instead. A Windows reader could resolve instar's string
  drive-relative, and a user who types a Windows path gets it
  labelled relative. *Mitigation:* nothing user-reachable emits these
  bytes while decision 8's guard stands, and the parent unicode name
  field — the only one libvhdi and qemu actually read — is
  unaffected. Settling it means reopening open question 3, which no
  oracle in reach can arbitrate, so it is recorded in
  `docs/quirks.md` and must be decided before the guard is removed
  rather than inherited by the phase that removes it.

## Definition of done

* `plan_vhd` with a `Dynamic` subformat and a backing reference
  returns a plan whose bytes, laid into a buffer, are accepted by
  `VhdFooter::parse`, `VhdDynamicHeader::parse` and
  `VhdParentInfo::parse`, with `disk_type == 4`, the parent name
  decoding to the input path, and the selected locator carrying
  `defect == None`.
* The negative control in 5e fails the locator validation when
  the data is deliberately misplaced, demonstrating the positive
  assertion is not vacuous.
* `plan_vhd` with no backing produces **byte-identical** output to
  `develop` at `f981374` for the same options, proven by a
  committed test rather than by inspection.
* `plan_vhd` with `VhdSubformat::Fixed` and a backing returns
  `CreateError::BackingFileUnsupported`.
* A 255-UTF-16-code-unit parent path succeeds and a 256-code-unit
  one is refused; a path containing a non-BMP character reaches
  that limit 2 code units at a time (127 such characters fit, 128
  do not). The last two bytes of the parent name field are always
  zero, asserted directly rather than assumed.
* `shared::utf8_to_utf16` and `shared::utf16_to_utf8` round-trip
  every case in the existing `utf16_to_utf8` test set.
* The same parent path encodes to two different byte strings under
  `big_endian = true` and `big_endian = false`, asserted by a test.
* `VhdFooter::timestamp` is read from offset 24, asserted against a
  footer whose timestamp differs from its size fields.
* `make test-rust` passes, `make check-binary-sizes` passes
  (no guest binary changes in this phase, so this is a regression
  check, not a budget question), and `pre-commit run --all-files`
  is clean.
* `fuzz_create_emitters` builds and runs against the new path
  without tripping its `minimum_file_size` or overlap oracles.
* `docs/create.md` is unchanged, and `create -f vpc -b` still
  fails with the same error a user sees today. **Verified by hand
  against a binary built from this branch**, not by a committed
  test: `create -f vpc parent.vhd 16M` succeeds and
  `create -f vpc -b parent.vhd -F vpc child.vhd 16M` gives
  `Error: "create failed: invalid option for target format"`,
  exit 1, with no child file written. There is no committed test
  for this and there cannot be one in `crates/create`: decision
  8's guard lives in the guest binary, which that crate's harness
  cannot reach. Permanent coverage belongs with the phase that
  adds the Python integration tests, and its brief should say so.

  **This replaces a Definition-of-done item that was impossible.**
  The plan originally required that `git diff develop...HEAD`
  touch no file under `src/operations/` or `src/vmm/`. It cannot:
  `VhdCreateOpts` is built by struct literal at seven sites, two
  of which are `src/operations/create/src/main.rs:310` and
  `src/fuzz/fuzz_targets/fuzz_create_emitters.rs:175`, so adding
  the parent-identity fields breaks compilation until they are
  updated. The survey missed this by reading `plan_vhd` and not
  its callers. What the criterion was protecting — that this phase
  changes no user-visible behaviour — is preserved by the explicit
  guard in decision 8 instead, and is the falsifiable thing to
  check.
* #566 is open, accurate, and linked from the master plan's
  Future work.

## Back brief

Before implementation starts, the management session should
confirm three things, because each is cheap to agree now and
expensive to redo after 5d:

1. **Decision 2** — that the all-zero footer UUID stays as it is
   in this phase and becomes an issue. This is the call most
   likely to be disagreed with, and reversing it after 5d means
   revisiting `plan_vhd`'s signature, the guest/host ABI, and
   every golden comparison in `tests/test_create.py`.
2. **Decision 1's fixed offset of 1536** for the locator data,
   and specifically that the BAT moves only for a differencing
   child. If the reviewer would rather the region sat after the
   BAT, that is a layout change touching 5d and 5e together.
3. **The intermediate state** — that this phase deliberately
   lands an emitter no user can invoke, with `create -f vpc -b`
   still refused until phase 7. If the operator would rather
   phases 5 and 7 landed as one pull request, the step plan needs
   to say so before 5d rather than after.
