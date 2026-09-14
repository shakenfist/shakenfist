# PLAN: Differencing phase 3 — parent-locator parsing

Phase 3 of [PLAN-differencing.md](/components/instar/plans/PLAN-differencing/).

## Goal

Teach `crates/vhd` and `crates/vhdx` to read the parent identity
and the parent locator table out of a differencing image, and to
stay bounds-check-clean when the table is hostile. Parse only:
nothing in this phase changes what any operation *does* with what
it now knows.

## Planning effort

High, as the master plan requires for this phase. The parse is
against a format spec, it introduces the first UTF-16 handling in
the tree, it runs in `no_std` with no allocator, and its inputs
include six fixtures built specifically to break it.

## Review effort

High, and concentrated in one place: every offset the parser
computes from a value read out of the image must be checked
against the image before it is used. The management session reads
the bounds checks themselves rather than the tests that exercise
them, because a test proves a case is handled and only the code
proves the class is.

## Scope

**In scope.**

* A strict UTF-16 to UTF-8 decoder in `shared`, big and little
  endian, writing into a caller-supplied buffer.
* In `crates/vhd`: the parent unique id, parent timestamp and
  parent unicode name from the dynamic header, plus the eight
  parent locator entries and the platform data each names.
* In `crates/vhdx`: the parent locator metadata item the crate
  already finds and currently discards — its linkage GUID and its
  key/value pairs.
* Unit tests, including one per adversarial shape from phase 2.

**Out of scope, and deliberately so.**

* **Wiring the parsed path into `info`'s `backing_file` field.**
  See survey finding 4: doing so silently turns on host-side VHD
  chain walking, which is phase 11's job. The `b"\0"` placeholders
  in the VHD and VHDX branches of `src/operations/info/src/main.rs`
  stay exactly as they are.
* Any change to what `VhdState::init` or `VhdxState::init` accept
  or refuse. That is phase 4, and keeping it there means the tree
  is never in a state where instar reads a differencing image
  differently than it did before but still composes it wrong.
* Emitting any of these structures — phases 5 and 6.
* Resolving, opening or following a parent path. The crates are
  `no_std` with no filesystem access, so this is structural rather
  than a promise.
* Fuzz targets for the new entry points. Phase 9 adds them; this
  phase only owes them an API shape they can be pointed at.
* Choosing what to do when locators disagree. This phase decides
  which entry *names* the parent (decision 5); what to do about it
  is phase 4.

## What the survey found

The master plan's phase 3 material is accurate. Every line
reference in the sequencing rationale still resolves after phases
1 and 2: `vhdx::build_header`'s DataWriteGuid derivation is at
`src/crates/vhdx/src/lib.rs:1341-1345` as claimed, `plan_vhdx`
passes sequence numbers 1 and 2 at `src/crates/create/src/lib.rs:964-966`,
`plan_vhd` writes `UUID_ZERO` at `:776` and `:820`,
`discover_backing_chain` is at `src/vmm/src/main.rs:2416`, and the
security knobs are at `src/vmm/src/config.rs:65` and `:67`. Issues
#547 and #548 are both still open. Nothing in the master plan
needed correcting for this phase, which is worth saying explicitly
because it is not the usual result.

Six findings the plan did not already record:

1. **The VHD crate parses none of it.** `VhdDynamicHeader`
   (`src/crates/vhd/src/lib.rs:177-184`) stops at `checksum`. The
   parent unique id, parent timestamp, parent unicode name and all
   eight locator entries are simply not read. There is no code to
   correct here, only code to add.

2. **The VHDX crate finds the item and throws it away.** The
   metadata scan already recognises `PARENT_LOCATOR_GUID` and
   records its offset (`src/crates/vhdx/src/lib.rs:432-434`), and
   then discards it four lines later:

   ```rust
   // If parent locator is found but we don't use it, that's fine.
   // We just note has_parent from file parameters flags.
   let _ = parent_loc_offset;
   let _ = found_parent_loc;
   ```

   (`:517-520`.) So the VHDX half of this phase is smaller than the
   VHD half: the item is already located, only its contents are
   unread.

3. **Both crates are `no_std` and are shared by the host and the
   guest.** `src/crates/vhd/src/lib.rs:7` and
   `src/crates/vhdx/src/lib.rs:11` are `#![no_std]`, and `src/vmm`
   depends on both (`src/vmm/Cargo.toml:35-36`) alongside the guest
   operations. One parser therefore serves host chain discovery and
   guest read paths both, which is the outcome we want — but it
   means no allocator, and it means a mistake lands on both sides
   of the sandbox boundary at once.

4. **Populating `info`'s `backing_file` would turn on host VHD
   chain walking as a side effect.** `discover_backing_chain` is
   format-agnostic about the chain step: it reads
   `info_result.backing_file` and, if it is `Some`, validates the
   path and loops (`src/vmm/src/main.rs:2569-2578`). The format
   gate above it (`:2508-2516`) only refuses formats that resolve
   to `ImageFormat::Unknown`, which `vpc` and `vhdx` do not. Today
   the VHD and VHDX branches of the info operation pass
   `b"\0".as_ptr()` for that field
   (`src/operations/info/src/main.rs:447` and `:462`), which is the
   only reason `instar info --chain` on a differencing VHD does not
   already walk. This is why the wiring is out of scope: it is a
   one-token change with a phase-11-sized consequence.

5. **There is no UTF-16 handling anywhere in the tree.** `grep -ri
   'utf16\|utf_16\|from_utf16' --include=*.rs src/` returns
   nothing, and `shared`'s helpers stop at endian integer readers
   (`src/shared/src/lib.rs:76-160`). This phase introduces the
   first one, which is why it gets its own step rather than being
   buried inside the VHD parser.

6. **Rust unit tests in this repository never read instar-testdata
   from disk.** They build structures in memory — `make_footer` at
   `src/crates/vhd/src/lib.rs:1158` is the local pattern — or embed
   a short byte literal with a comment naming the fixture it came
   from, as `src/shared/src/format_detection.rs:571` and `:581` do.
   The format crates are `no_std` and must test without the fixture
   repository present. Phase 3's tests therefore construct their
   own headers; the phase 2 fixtures are consumed for real by phase
   8's integration tests, not here.

The `Merged` cell for phase 2 in the master plan still read
`instar: pending`, which the second review round of #552 noted as
unfixable from inside that PR. This planning commit fills it in
with `1a677c77` (#552).

## Decisions

1. **The parse lives in the two format crates, not in a new one
   and not on the host.** Both crates are already the single
   parser for their format on both sides of the sandbox boundary
   (survey finding 3). A host-side parser written for chain
   discovery would be a second implementation of the same
   structure, and the two would drift the first time an offset was
   corrected in one of them.

2. **UTF-16 decoding goes in `shared`, strict, into a caller
   buffer.** Signature:
   `utf16_to_utf8(src: &[u8], big_endian: bool, dst: &mut [u8]) -> Option<usize>`.
   It refuses — returns `None` — on an unpaired surrogate, on an
   odd input length, and when the output does not fit. It does
   **not** substitute `U+FFFD`.

   Strictness is the decision, and lossy replacement is the more
   common default, so the reasoning matters. A parent locator is a
   security boundary: the string is going to be resolved against an
   allowlist and opened by the host in phase 11. Substituting a
   replacement character manufactures a path that was not in the
   image and hands the allowlist something to match on that the
   attacker did not write. Refusing is also what the phase 2
   corpus expects — `vhd-diff-locator-conflicting.vhd` carries a
   `Mac ` blob built specifically to decode as nothing in any
   encoding, and a lossy decoder turns that fixture into a silent
   pass. `shared` rather than either crate because VHD needs big
   endian and VHDX little, and phases 5 and 6 need the encode
   direction of the same pairing.

3. **The 512-byte parent-name field is the bound; a missing
   terminator is not an error.** Read at most 512 bytes from
   header offset +64, stop early at a `0x0000` code unit if there
   is one, and never look past the field for a terminator. This is
   the exact defect libvhdi has (phase 1 defect C: it over-reads
   two bytes into the following locator table and reports a 257th
   character), and `vhd-diff-locator-overlong.vhd` exists to catch
   it. 256 code units with no terminator is a valid, complete
   name.

4. **Every offset read from the image is validated before use,
   and a malformed entry is reported rather than skipped.** A
   locator entry whose `platform_data_offset` plus
   `platform_data_length` exceeds the image, or which overlaps the
   footer or dynamic header, or whose `data_length` exceeds its
   `data_space`, yields an entry marked malformed with its raw
   fields preserved. It does not yield `None` for the whole table
   and it is not silently dropped: phase 4 needs to be able to say
   *why* it is refusing, and a dropped entry is indistinguishable
   from an absent one.

5. **Phase 3 decides which entry names the parent; phase 4
   decides what to do about it.** The parser exposes the full
   eight-entry table faithfully, and additionally a
   `preferred_locator()` that selects by platform code — `W2ru`
   (relative) before `W2ku` (absolute), the deprecated `Wi2r` and
   `Wi2k` after those, non-Windows codes never — and that returns
   a distinguishable "ambiguous" answer when two entries share a
   platform code and their decoded strings differ.

   This is the decision most likely to be argued with, because it
   puts something that smells like policy in a phase named parse.
   The argument for it: *which entry names the parent* is a fact
   about the format, settled by the platform codes, and if phase 3
   does not settle it then phase 4 and phase 11 each invent an
   answer and the host and the guest can disagree about what a
   chain is. What stays in phase 4 is the part that really is
   policy — whether an ambiguous table is refused, warned about,
   or resolved to the relative entry. The second review round of
   #552 asked for exactly this to become an explicit phase 3
   deliverable rather than a question left in a manifest
   description, and this is that.

6. **Nothing is wired to a consumer in this phase.** Survey
   finding 4 is the reason. The falsifiable form of this is in the
   definition of done: `instar info` output on every fixture in
   the manifest is byte-identical before and after the phase.

7. **Entry points take a byte slice and return an `Option`, with
   no I/O.** `parse_parent_locators(header: &[u8]) -> Option<...>`
   rather than a call-table reader, so phase 9 can point a fuzz
   target at it the way `fuzz_vhd_footer` points at
   `VhdFooter::parse`. Where locator data lives outside the header
   the caller supplies the bytes; the parser never reads a sector
   itself.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | medium | opus | worktree | Add `utf16_to_utf8(src: &[u8], big_endian: bool, dst: &mut [u8]) -> Option<usize>` to `src/shared/src/lib.rs`, near the endian integer helpers at `:76-160`. `no_std`, no allocation, returns the number of UTF-8 bytes written. Return `None` on: odd `src` length; an unpaired high or low surrogate (a high surrogate `0xD800..=0xDBFF` not followed by a low `0xDC00..=0xDFFF`, or a low surrogate reached first); output that does not fit `dst`. Do **not** substitute `U+FFFD` — decision 2 explains why, and a reviewer will check for this specifically. Stop at the first `0x0000` code unit and do not include it in the count. Unit tests: ASCII round trip in both endiannesses; a surrogate pair (e.g. `U+1F600`) in both; an unpaired high surrogate refused; an unpaired low surrogate refused; odd length refused; a `dst` one byte too small refused; the exact 25-byte `Mac ` blob from `vhd-diff-locator-conflicting.vhd` (`00 d8 d8 00 00 96 00 02` + `conflict-six.vhd` + `00`) refused in both endiannesses — that fixture exists to be refused and this is the test that proves it is. Follow the existing test style in that file. Nothing else in `shared` changes. |
| 3b | high | opus | worktree | Add parent parsing to `src/crates/vhd/src/lib.rs`. Extend `VhdDynamicHeader` (`:177`) — or add a sibling struct, your call, but say which and why in the commit message — with `parent_unique_id: [u8; 16]`, `parent_timestamp: u32`, and a parsed parent name. Offsets, all header-relative and all big-endian, are pinned in `docs/plans/PLAN-differencing-phase-01-pin.md` under "VHD — the dynamic header of a differencing child": parent unique id `+40` (16 bytes, opaque, do not byte-swap), parent timestamp `+56` (BE u32), parent unicode name `+64` (512 bytes, **UTF-16 BIG endian** — the opposite of the locator data, this is the single most likely thing to get wrong), locator entries `+576` (8 × 24 bytes). Do not rediscover these; that section is the authority and every row in it is backed by an `xxd` of a Hyper-V image. Then add the eight-entry locator table per "VHD — the eight parent locator entries": platform code `+0` (4 ASCII bytes, **not** byte-swapped), platform data space `+4` (BE u32, a **byte** count despite the spec's wording — see the "Where SPEC(VHD) and Hyper-V disagree" subsection), data length `+8` (BE u32), reserved `+12`, data offset `+16` (BE u64, absolute in the file). Locator platform data is **UTF-16 LITTLE endian** for `W2ku`/`W2ru`/`Wi2k`/`Wi2r`, UTF-8 for `MacX`, and opaque for `Mac `; decode only the first group, and only via `shared::utf16_to_utf8` from step 3a. Apply decisions 3, 4, 5 and 7: bound the name at 512 bytes and never scan past it, validate every offset against the supplied image length before use and mark rather than drop malformed entries, provide `preferred_locator()` with `W2ru` > `W2ku` > `Wi2r` > `Wi2k` and an ambiguous result when two entries share a code and disagree, and take byte slices with no I/O. Change nothing about `VhdState::init` — note `:578` accepts `DISK_TYPE_DIFFERENCING` today and treats it as dynamic, which is issue #547 and phase 4's to fix, not yours. Tests are in-memory only: extend the `make_footer` pattern at `:1158` with a `make_dynamic_header` helper; do not read instar-testdata, the crate is `no_std` and must test without it. Cover the six phase 2 adversarial shapes as constructed headers: absolute path, `../../../etc/passwd`, UNC, URL, a 256-code-unit name with no terminator, and a table with eight mutually disagreeing entries including two sharing a platform code. Also: a data offset past the end, a data offset overlapping the footer, `data_length` > `data_space`, and a platform code of four zero bytes in a slot after a populated one. |
| 3c | high | opus | worktree | Add parent locator item parsing to `src/crates/vhdx/src/lib.rs`, replacing the discard at `:517-520`. The item's offset is already recorded at `:432-434`, so this is contents-only. The layout is pinned in `docs/plans/PLAN-differencing-phase-01-pin.md` under "VHDX — the parent locator metadata item": a 16-byte linkage GUID, then `key_value_count` (LE u16), then that many 12-byte entries of key offset / value offset / key length / value length, all offsets relative to the item start, keys and values UTF-16 **little** endian. Decode via `shared::utf16_to_utf8`. The keys instar cares about are `parent_linkage`, `relative_path`, `volume_path` and `absolute_win32_path`; see "VHDX — which keys instar should write" for which are load bearing. Apply decisions 4 and 7: every offset validated against the metadata region bounds before use, malformed entries marked rather than dropped, byte slice in and no I/O — read the item's bytes through the existing sector-reading path in `parse_vhdx_metadata` and hand the parser a slice, do not add call-table reads inside the parser. Two constraints worth stating: `parent_linkage` is a **braced, case-insensitive** GUID string (the master plan's sequencing rationale explains that instar's own writers make this check vacuous, which is phase 6's problem, not yours — parse it faithfully and compare case-insensitively), and do **not** change the `metadata.has_parent` rejection at `:843`, which is phase 4's. Tests in-memory in the existing style: a well-formed item; a `key_value_count` larger than the region can hold; a value offset past the region end; a value length that overflows when added to its offset; a duplicate key; a key that decodes as nothing. |
| 3d | medium | sonnet | none | Close the phase. Verify the definition of done item by item and report each. The no-behaviour-change item is the load-bearing one and it is mechanical: build with `make instar`, then for every image in `tests/manifest.json` run `instar info` and `instar info --output json` and diff against the same command on the `develop` binary — the output must be byte-identical, and in particular no VHD or VHDX image may gain a `backing file` line. Write that comparison as a script in the plan directory or `tools/` rather than doing it by hand, since phase 4 will want to run it again. Then set phase 3 to Complete in `docs/plans/PLAN-differencing.md` and `docs/plans/index.md`, fill the `Merged` cell, and present the commits. Do not commit. |

## Risks and mitigations

* **The name field endianness gets used for the locator data, or
  vice versa.** VHD stores the parent unicode name UTF-16 big
  endian at header `+64` and the locator platform data UTF-16
  little endian at the offsets the entries name. This is genuinely
  unusual and it is the mistake this phase is most likely to make.
  *Mitigation:* step 3b's brief states both explicitly; the
  management session checks the two call sites of
  `utf16_to_utf8` in the VHD parser pass different `big_endian`
  values, and rejects the step if they match.

* **The parse gets wired to a consumer "because it is only one
  line".** Survey finding 4 makes that one line turn on host VHD
  chain walking. *Mitigation:* the definition of done's
  byte-identical `info` comparison catches it mechanically, and
  step 3d writes it as a script rather than a promise.

* **A bounds check is written as a test rather than as code.**
  Six adversarial fixtures make it tempting to believe coverage is
  the safeguard. *Mitigation:* stated review effort — the
  management session reads the arithmetic, specifically that every
  `offset + length` is computed with `checked_add` against the
  supplied slice length before any indexing, and that no test's
  passing is accepted as evidence for the class it samples.

* **A sub-agent asserts a plausible-but-wrong spec fact.** This
  repository's standing warning, and the reason phase 1 produced a
  structure pin at all. *Mitigation:* both parser briefs cite the
  pin by section and forbid rediscovery; any claim in a sub-agent's
  report that contradicts the pin is treated as the sub-agent being
  wrong until an `xxd` says otherwise.

* **`no_std` is forgotten and something reaches for `String` or
  `format!`.** *Mitigation:* it will not compile, and
  `make check-binary-sizes` guards the 768KB cap besides. Listed
  because the failure is confusing rather than because it is
  likely.

## Definition of done

* `shared::utf16_to_utf8` exists, is `no_std`, allocates nothing,
  and refuses unpaired surrogates, odd lengths and overlong output
  rather than substituting `U+FFFD`. The `Mac ` blob from
  `vhd-diff-locator-conflicting.vhd` is refused in both
  endiannesses, proven by a test that embeds those 25 bytes.
* `crates/vhd` exposes the parent unique id, parent timestamp,
  parent unicode name and all eight locator entries of a
  differencing VHD, and `crates/vhdx` exposes the parent locator
  item's linkage GUID and key/value pairs.
* `preferred_locator()` returns `W2ru` over `W2ku` on a table
  carrying both, and returns its ambiguous result on a table where
  two entries share a platform code and disagree.
* A 256-code-unit parent name with no NUL terminator parses to
  exactly 256 characters, and the parser reads no byte at header
  offset +576 or beyond while doing it.
* Every offset derived from image data is bounds-checked with
  `checked_add` against the slice length before indexing. This is
  verified by reading the code, not by the tests passing.
* `instar info` and `instar info --output json` produce
  byte-identical output on every image in `tests/manifest.json`
  before and after this phase, and the comparison is a committed
  script rather than a claim. No VHD or VHDX image gains a backing
  file line.
* **Verbose output (`instar info -v`) is deliberately outside that
  claim**, and the script does not compare it. Staging a VHDX
  parent locator item costs sector reads, which land in
  `bytes_read`, which reaches `send_complete` and the host's
  verbose formatter but never the info text or the JSON. That is a
  resource-accounting difference, not a behaviour change, and a
  verbose comparison would report it as a failure. The
  `has_parent` gate added in review narrows it further: only an
  image that actually claims a parent pays the reads, so verbose
  output for every other image is unchanged too.
* Documentation of the new public surface is **deliberately held to
  phase 10**, which the master plan makes the documentation phase.
  Nothing user-visible changed here, so `docs/format-internals.md`
  gaining a parent-locator section now would document an API no
  operation calls. Recorded so a later reviewer reads the gap as a
  decision rather than an oversight.
* `git diff --name-only develop...HEAD -- src/operations/` is
  empty: no operation changed.

  **Not true as merged, corrected in retrospect.** The review
  round that bounded a VHDX locator to its metadata region gave
  `parse_metadata` a `metadata_length` parameter, which changed
  the one caller outside the crate:
  `src/operations/check/src/main.rs` gained
  `let metadata_length = regions[1].length;` and passes it
  through. That is a call-site edit with no behaviour change --
  `check` does nothing new with the value -- but the criterion as
  written is false against `42e879f`, and the honest record is
  that the bullet was not revisited when the review change
  landed.
* `VhdState::init` still accepts `DISK_TYPE_DIFFERENCING` and
  `VhdxState::init` still rejects `has_parent`, unchanged. Issues
  #547 and #548 are still open.
* `make instar` builds, `make check-binary-sizes` passes,
  `make test-rust` passes, and `pre-commit run --all-files`
  passes.

## Back brief

Before executing any step, back brief the operator on the
understanding of this phase and how the intended work aligns with
it.

Step 3b additionally gates: show the management session the
struct shape and the `preferred_locator()` signature before
writing the parser body. Decision 5 is the one this plan expects
to be argued with, and the shape of the return type is where that
argument becomes concrete — it is cheap to settle before the
tests are written and expensive afterwards.
