# `instar bitmap` subcommand

## Status: Complete

Phases 1-10. `instar bitmap` — full qcow2 persistent-dirty-bitmap
management (add/remove/clear/enable/disable/merge), qcow2-oracle
integration tests + bit-for-bit merge validation, cross-version
baselines, coverage + differential fuzzing, and docs; pre-existing
resize bitmap-data-loss defect fixed. Leaves `bench` the only
unimplemented qemu-img subcommand.

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on external concepts (the qcow2 persistent-dirty-bitmap on-disk
format, `qemu-img bitmap` semantics, refcount management, KVM,
virtio), research as needed to give a confident answer. Flag any
uncertainty explicitly rather than guessing.

All planning documents go in `docs/plans/`. Phase plans for this
master plan are named `PLAN-bitmap-phase-NN-<descriptive>.md`
alongside this file and linked from the Execution table below.
They are *not* added to `docs/plans/order.yml` — only the master
plan is.

Consult `ARCHITECTURE.md` for the overall system structure (host
VMM, KVM guest, call table, device emulation). Consult `AGENTS.md`
for build commands, project conventions, code organisation, and the
security-model summary. Consult `docs/qcow2/` for the qcow2 format
notes and `docs/commentary/` for design rationale. The closest
prior art for this plan is **`PLAN-amend.md`** (an in-place qcow2
header mutation) and **`PLAN-snapshot.md`** (in-place qcow2
metadata mutation with refcount management and a directory/table
structure). Read both before planning any phase.

I prefer one commit per logical change, and at minimum one commit
per phase. Each commit should be self-contained: it should build,
pass tests, and have a clear commit message explaining what changed
and why.

## Situation

`qemu-img`'s subcommand roster is, with this plan, all but
complete in instar. The convert-followups set (`measure`, `create`,
`resize`, `rebase`, `commit`, `map`, `snapshot`) shipped, then
`check --repair` (`PLAN-check-repair.md`), then `amend`
(`PLAN-amend.md`). Comparing instar's CLI against the installed
`qemu-img 10.0.8`, **exactly two qemu-img subcommands remain
unimplemented: `bitmap` and `bench`.** (instar also has two
non-qemu-img extras, `copy` and `config`.)

Of the two, **`bitmap` is the right next pickup and `bench` should
be deferred or skipped.** `bitmap` is a qcow2 *structural mutation*
— the same family as the already-shipped `amend`, `snapshot`,
`commit`, `rebase`, and `check --repair` — so it slots directly
into instar's established host→guest mutation architecture and the
byte-for-byte cross-validation-against-`qemu-img` test pattern.
More importantly, it is the one of the two that real platforms
depend on: `docs/usage.md` records oVirt (VDSM, ovirt-imageio) as
using `bitmap` for incremental-backup workflows. `bench`, by
contrast, is a live I/O performance harness — it neither inspects
nor mutates image structure, orchestration platforms rarely invoke
it, and it does not fit the "safe sandboxed `qemu-img` replacement"
mission. `bench` is recorded under Future work; this plan is
`bitmap` only.

**What `qemu-img bitmap` does.** It manages *persistent dirty
bitmaps* stored inside a qcow2 image. A dirty bitmap records, at a
configurable granularity, which regions of the virtual disk have
changed — the foundation of incremental backup. The subcommand has
six actions, all on a single named bitmap in a single image:

```
bitmap (--add | --remove | --clear | --enable | --disable | --merge SOURCE)...
       [-g GRANULARITY] [-b SOURCE_FILE [-F SOURCE_FMT]]
       [--object OBJDEF] [--image-opts | -f FMT] FILENAME BITMAP
```

- `--add` — create a new, enabled, persistent, empty bitmap
  (optionally with `-g GRANULARITY`).
- `--remove` — delete a bitmap, freeing its clusters.
- `--clear` — reset all of a bitmap's bits to zero.
- `--enable` / `--disable` — toggle whether the bitmap auto-tracks
  writes (the persistent `auto` flag).
- `--merge SOURCE` — OR another bitmap's set bits into the target
  (the source may live in another file via `-b`/`-F`).

Crucially (verified against the qemu 10.0 source — see the
research notes folded into Open questions): **actions are applied
in command-line order, are repeatable, and may be combined in one
invocation** (e.g. `--add --disable` creates-then-disables). There
is **no `--list`, no `-q`, and no `--force`** on `qemu-img bitmap`
in 10.0 — listing is `qemu-img info`'s job, and there is no
override flag. On success the command is **silent** (no stdout).

**The qcow2 on-disk shape** (qcow2 spec §"Bitmaps", verified
against `block/qcow2-bitmap.c` at v10.0.0):

- A header **autoclear feature bit 0** (`autoclear_features`,
  header bytes 88–95) guards bitmap consistency: set ⇔ the bitmaps
  extension is valid; if the extension is present but the bit is
  clear, the extension is treated as inconsistent/absent.
- A **bitmaps header extension** (type `0x23852875`, 24-byte data:
  `nb_bitmaps`, reserved, `bitmap_directory_size`,
  `bitmap_directory_offset`).
- A **bitmap directory** of variable-length entries (24-byte fixed
  head: `bitmap_table_offset`, `bitmap_table_size`, `flags`,
  `type`, `granularity_bits`, `name_size`, `extra_data_size`; then
  extra_data, then the non-NUL-terminated name, zero-padded to 8
  bytes). Flags: bit 0 `in_use`, bit 1 `auto` (= enabled), bit 2
  `extra_data_compatible` (qemu treats bits 2–31 as reserved and
  rejects them — instar must only ever emit bits 0/1).
- A **bitmap table** (cluster-aligned array of big-endian u64
  entries; bits 9–55 = host cluster offset, bit 0 with a zero
  offset distinguishes all-zeros vs all-ones clusters).
- **Bitmap data clusters** holding the actual bits; bit *n* covers
  bytes `[n·gran, (n+1)·gran)`. Default granularity =
  `MIN(65536, MAX(4096, cluster_size))`; qemu permits
  `granularity_bits` in 9–31 (512 B – 2 GiB/bit). Bitmaps require
  qcow2 **v3** (v2 cannot store them); name ≤ 1023 bytes; ≤ 65535
  bitmaps.

Every table cluster, data cluster, and directory cluster is a
normal **refcounted** allocation — so add/remove/clear/merge must
keep the refcount tree consistent or `qemu-img check` will report
leaks. qemu rewrites the whole directory + extension on
content/count changes (`update_ext_header_and_dir`), and uses a
crash-safe **clear-autoclear → rewrite-in-place → set-autoclear**
dance for pure flag flips (`..._in_place`).

The relevant existing infrastructure this plan builds on:

- **In-place qcow2 mutation idiom.** `amend`, `snapshot`,
  `resize`, `rebase`, `commit`, and `check --repair` all establish
  the host→guest pattern: host probes format, opens the file
  `O_RDWR`, attaches it as the virtio-block *output* device,
  populates a `*Config` at `OPERATION_CONFIG_ADDR`, launches the
  guest op, harvests a `*Result` over the call table, renders
  human/json. `run_amend`/`run_amend_guest` and
  `run_snapshot_*` (`src/vmm/src/main.rs`) are the reference flows.
- **Refcount + directory/table mutation prior art.** `snapshot`
  (`src/crates/snapshot/`) is the closest analog: it manages a
  qcow2 directory-of-variable-length-entries plus per-entry tables
  and updates refcounts on cluster alloc/free. Its refcount
  mutators and table serializers are the model (and likely partly
  reusable) for the bitmap table/directory + cluster
  allocate/free.
- **qcow2 crate header-extension walk.** `parse_header_extensions`
  in `src/crates/qcow2/src/lib.rs` already walks v3 header
  extensions and knows `EXT_BACKING_FORMAT`,
  `EXT_EXTERNAL_DATA_FILE`, `EXT_ENCRYPT_HEADER`. It does **not**
  yet know `EXT_BITMAPS` (`0x23852875`) or parse the bitmap
  directory — that is net-new (Phase 1). The autoclear-features
  field is already parsed.
- **No existing dirty-bitmap parsing.** The only "bitmap" code in
  the qcow2 crate today is *subcluster* bitmaps in extended-L2
  entries (`SubclusterBitmapStatus`, `validate_subcluster_bitmap`)
  — a completely unrelated structure. There is nothing to extend;
  the directory/table/data parsing is built fresh.
- **Call-table / ABI boundary.** `src/shared/src/lib.rs` holds the
  `repr(C)` `*Config`/`*Result` structs and the `CallTable`
  function pointers; `CallTable::VERSION` is **18** (amend bumped
  it 17→18). `crates/guest-protocol/proto/guest.proto` holds the
  `GuestMessage` oneof. bitmap adds `BitmapConfig`,
  `BitmapResult`, a `send_bitmap_result` pointer (VERSION → 19),
  and a proto result message.
- **Memory map headroom.** `OPERATION_LOAD_ADDR` is `0x22000`;
  core's budget is `0x10000`–`0x22000` (72 KiB), validated
  `.bss`-inclusively by `scripts/check-binary-sizes.sh`. The amend
  work left core near the *old* 64 KiB ceiling, then raised the
  ceiling to 72 KiB — so there is now ~8 KiB of headroom for the
  one new result sender, but this must be re-measured in Phase 2.
- **Option parsing.** `parse_o_options` and the `--object` /
  `--image-opts` "not yet supported" refusal posture
  (`src/vmm/src/main.rs`) are reused; `bitmap`'s surface is
  action-flag-driven rather than `-o`-driven, so the clap layout
  differs from amend's.
- **Test / baseline / fuzz harnesses.** Python integration tests
  in `tests/test_*.py` (cross-checked against `qemu-img` as
  oracle); cross-version baselines via
  `instar-testdata/scripts/generate-baselines.py`; coverage fuzz
  targets in `src/fuzz/fuzz_targets/`; differential fuzz `op_*`
  functions in `scripts/differential-fuzz.py`. Note the qcow2
  unit-test feature gate: `cargo test -p qcow2 --features create`.

## Mission and problem statement

Implement `instar bitmap` such that:

1. It accepts a subset of `qemu-img bitmap`'s surface, matching its
   CLI grammar:
   ```
   instar bitmap (--add | --remove | --clear | --enable | --disable
                  | --merge SOURCE)... [-g GRANULARITY]
                  [-b SOURCE_FILE [-F SOURCE_FMT]] [-f FMT]
                  [--output {human,json}] FILENAME BITMAP
   ```
   - **Actions are repeatable and applied in command-line order**,
     matching qemu. `--add` and `--merge` carry data (`-g`,
     `SOURCE`); the rest are bare.
   - `-g GRANULARITY` is valid only with `--add` (error otherwise,
     matching qemu's `"granularity only supported with --add"`).
   - `-b SOURCE_FILE` / `-F SOURCE_FMT` are valid only with
     `--merge` (matching qemu's two ordering errors).
   - `-f FMT` forces target format detection.
   - On success instar is **silent** by default (matching qemu).
     `--output json` is an instar-only extension emitting a
     machine-readable summary of the applied actions for tooling;
     it does not exist in qemu and must not appear on the default
     path. (Decide in Phase 5 whether to keep `--output human` as a
     no-op alias or omit it — see Open question 11.)
   - `--object` / `--image-opts` are **rejected at runtime** with a
     clear "not yet supported" error, the same posture
     `create`/`resize`/`amend` took.
   - There is intentionally **no `--list`, no `-q`, no `--force`**
     — they do not exist on `qemu-img bitmap` 10.0.

2. It is **qcow2-only**. Non-qcow2 inputs (raw, vmdk, vhd, vhdx)
   are rejected with a clear error (qemu-img bitmap is qcow2-only
   too — and v2 qcow2 is refused with the qemu-comparable "Cannot
   store dirty bitmaps in qcow2 v2 files"). Mirrors `snapshot` and
   `amend`.

3. The in-scope mutations are applied safely in place, keeping the
   refcount tree and the autoclear bit consistent:
   - **`--add`** — append a directory entry for a new, enabled
     (`auto` set, `in_use` clear), empty bitmap; allocate and
     zero-fill its bitmap table cluster(s) (an empty bitmap has a
     **zero table and no data clusters**); set the autoclear
     bitmaps bit; create the bitmaps extension if this is the first
     bitmap. Refuse if the name already exists, the name exceeds
     1023 bytes, the granularity is out of the 9–31 bit range, or
     the image is qcow2 v2.
   - **`--remove`** — free the bitmap's table and data clusters
     (refcounts decremented), drop its directory entry, decrement
     `nb_bitmaps`, rewrite the directory; if it was the last
     bitmap, clear the autoclear bit and zero the extension fields.
     This is the only action allowed on an `in_use`/inconsistent
     bitmap.
   - **`--clear`** — reset all bits to zero: free data clusters,
     zero the table; reads return zeros.
   - **`--enable` / `--disable`** — flip only the `auto` flag via
     the crash-safe in-place dance (clear autoclear → rewrite dir →
     set autoclear).
   - **`--merge SOURCE`** — OR the source bitmap's set bits into
     the target, reallocating data clusters to cover the union.
     Same-image merge is in scope; cross-file merge (`-b`) is
     gated on Open question 2.
   - When multiple actions are given, apply them in order against
     the evolving on-disk state, matching qemu.

4. It cross-validates against `qemu-img bitmap`: after an instar
   bitmap operation, `qemu-img check` passes (no leaks/corruption),
   `qemu-img info` reports the same bitmap set/flags/granularity,
   and a bitmap produced by instar is byte-equivalent (or
   info/check-equivalent, per a documented divergence registry) to
   one produced by the same `qemu-img bitmap` invocation.

5. It is exercised by Rust unit tests (directory/table parse +
   serialize round-trips, planner patch computation), Rust
   round-trip tests, Python integration tests against the qemu
   oracle, cross-version baselines, coverage-guided fuzzing of the
   parser and planner, and differential fuzzing against
   `qemu-img bitmap`.

**Out of scope for v1** (see Future work): `qemu-img bench`;
non-qcow2 bitmap storage; bitmap migration/persistence semantics
beyond the on-disk flags; any `--object`/`--image-opts` handling
beyond rejection; and (pending Open question 2) cross-file
`--merge -b`.

## Open questions

These need resolution during detailed (phase) planning. Each is a
real fork in the design, grounded in what the code does today and
the qemu 10.0 source behaviour established during research.

1. **Ordered multi-action ABI.** qemu applies actions in CLI order
   within one invocation. Should v1 support multiple actions per
   invocation, or restrict v1 to exactly one action and defer
   combinations? Supporting the ordered list is how qemu works and
   the natural test surface (`--add --disable`), but it complicates
   the `BitmapConfig` ABI (a fixed-capacity ordered array of action
   opcodes + per-action operands) and the guest dispatch loop.
   **Leaning toward supporting an ordered list from the start** with
   a small fixed capacity (e.g. 8 actions), since the guest applies
   them by looping the same per-action primitives — but confirm the
   ABI cost against the `core.bin`/config-size budget in Phase 2.

2. **Cross-file merge (`-b SOURCE_FILE`).** `--merge` may name a
   bitmap in *another* image, which qemu opens read-only. instar's
   virtio model supports an input device distinct from the output
   device, but wiring a second read-only source image through the
   guest (and reading *its* bitmap directory/table/data) is
   materially more work than same-image merge. **Recommend:
   same-image `--merge SOURCE` in v1; cross-file `--merge -b`
   deferred** unless Phase 5's input-device wiring proves cheap.
   Decide in master-plan review; if deferred, `-b` is rejected with
   a clear "not yet supported" message and recorded in Future work.

3. **Should `instar info` learn to report bitmaps?** qemu-img info
   lists bitmaps (name, granularity, flags); instar's `info` does
   not parse the bitmaps extension today. The Phase 1 parser makes
   this nearly free, and it (a) is a real qemu-img parity gap and
   (b) gives the integration tests a native way to assert results
   rather than relying solely on the qemu oracle. **Recommend
   adding minimal bitmap reporting to `info` as part of Phase 1**,
   but treat it as optional — the qemu oracle is sufficient for
   correctness. Confirm whether matching qemu-img info's exact
   bitmap output formatting is in scope or a separate parity task.

4. **Cross-cutting bitmap preservation by *other* operations.** Do
   `resize`, `amend`, `commit`, `rebase`, and `check --repair`
   currently preserve the bitmaps header extension and the
   autoclear bit when they rewrite the qcow2 header/metadata, or do
   they silently drop bitmaps? If they drop them, that is a
   pre-existing data-loss bug this work surfaces. **Investigate in
   Phase 1** (read each op's header-write path); fix in-scope if
   small, otherwise file under Future work / Bugs and at minimum
   document. The autoclear bit's whole purpose is to let a
   bitmap-unaware writer safely invalidate bitmaps, so dropping
   them may be "spec-legal" but is still surprising.

5. **Crash-safety: which store path per action.** qemu uses two
   paths: a full directory+extension rewrite to *new* clusters
   (add/remove/clear/merge) and an in-place rewrite guarded by the
   clear→write→set autoclear dance (pure `enable`/`disable` flag
   flips). Confirm instar implements **both**, and decide the exact
   write ordering and fsync points (the host fsyncs after the guest
   halts, as amend established). A torn write must leave either the
   old valid bitmap state or a state the autoclear bit marks
   inconsistent — never a silently-corrupt bitmap presented as
   valid.

6. **Refcount machinery reuse.** How much of `src/crates/snapshot/`
   (and the `check --repair` refcount mutators) can the bitmap
   planner reuse for cluster allocate/free + refcount-table
   updates, versus needing bitmap-specific allocation? Identify the
   reusable surface in Phase 1/3 so the planner crate (Phase 3)
   does not reinvent refcount management. The free-cluster search
   and refcount-block growth are the load-bearing pieces.

7. **Granularity default and validation.** Confirm instar computes
   the default granularity identically to qemu
   (`MIN(65536, MAX(4096, cluster_size))`, falling back to 65536),
   and enforces `granularity_bits ∈ [9, 31]` with the
   qemu-comparable min/max error messages. Confirm the bitmap-data
   size and table-size formulas
   (`bytes = ceil(ceil(vsize/gran)/8)`, table entries =
   clusters spanning that serialization) match qemu's
   `get_bitmap_bytes_needed` / `size_to_clusters`.

8. **Empty-bitmap representation.** Confirm a freshly `--add`ed
   enabled bitmap is stored with a zero-filled table and **no data
   clusters** (only the table cluster(s) allocated and refcounted),
   matching qemu's lazy `store_bitmap_data`. This keeps `--add`
   cheap and is the baseline the round-trip tests assert.

9. **ABI footprint.** `BitmapConfig`/`BitmapResult` shapes: the
   config must carry the target format, the ordered action list
   (opcodes + per-action operands), the bitmap name (≤ 1023 bytes
   — sizeable), the granularity, and `--merge` source name(s) /
   source-file presence; plus the host-probed cross-check (version,
   cluster_size, refcount_bits, virtual_size, autoclear features,
   existing bitmap-directory summary). The result returns an
   action/error code and a per-action outcome summary. Decide the
   resize/amend model (host probes + passes a cross-check, guest
   re-parses and validates) — almost certainly yes — and bound the
   name/source storage. Names up to 1023 bytes × several actions
   could be large; consider whether the host pre-resolves and
   passes only what the guest needs.

10. **Inconsistent / `in_use` bitmaps.** On open, a bitmap with the
    on-disk `in_use` flag set is inconsistent; qemu refuses every
    action on it except `--remove`. Confirm instar adopts the same
    rule (and that, with no `--force` flag, there is no override),
    and that instar never *writes* a bitmap with `in_use` set on a
    clean store (qemu writes `auto`-only flags). Decide the error
    messages and the divergence posture if any differ from qemu.

11. **`--output` surface.** qemu-img bitmap has no `--output` and
    is silent on success. instar's other subcommands offer
    `--output {human,json}`. Decide whether `instar bitmap` keeps
    the flag (json as a tooling extension, human ⇒ silent) or omits
    it entirely for closest parity. **Lean toward keeping
    `--output json` as an opt-in extension, silent otherwise.**

12. **`core.bin` budget re-measure.** Adding `send_bitmap_result`
    to the call table grows core. The post-amend ceiling is 72 KiB
    with ~8 KiB headroom, so it should fit — but Phase 2 must build
    core and confirm `scripts/check-binary-sizes.sh` (the
    `.bss`-inclusive extent check) still passes, and if not, decide
    between trimming, moving result strings off-wire (as amend
    did), or lifting the memory-map budget again.

## Execution

Phase plans are written one at a time, at the recommended effort,
and reviewed before the next is drafted. The phase breakdown
mirrors the established subcommand-plan shape (parse → ABI →
planner → guest → host → tests → baselines → fuzz → docs), with an
**extra leading parsing phase** versus amend because the qcow2
bitmap directory/table/data structures are net-new to instar
(amend reused existing header parsing).

| Phase | Plan | Status |
|-------|------|--------|
| 1. qcow2 bitmap-structure parsing (`src/crates/qcow2/`): `EXT_BITMAPS` constant, bitmaps header-extension parse, directory-entry + table-entry + flags parse/serialize, granularity/geometry helpers, inline unit tests with fixtures; investigate bitmap preservation by other ops (OQ4); optionally surface bitmaps in `info` (OQ3) | PLAN-bitmap-phase-01-parse.md | Complete (steps 1a-1d; 1e info-listing deferred) |
| 2. ABI: `BitmapConfig` (ordered action list, name, granularity, merge source, host cross-check) / `BitmapResult`, call-table `send_bitmap_result` (VERSION 18→19), proto result message, magic/action-opcode/flag/error constants; re-measure `core.bin` budget (OQ1, OQ9, OQ12) | PLAN-bitmap-phase-02-abi.md | Complete (steps 2a-2e; core.bin at 66.7/72 KiB, 5.3 KiB headroom) |
| 3. bitmap planner crate (`src/crates/bitmap/`): per-action computation (add/remove/clear/enable/disable/merge) as pure in-place slice mutators (not a patch list), cluster allocate/free + refcount updates (reusing `snapshot::qcow2` mutators; 16-bit-only, no refcount-table growth ⇒ refuse), directory + extension serialization, all validation (v2 refuse, name exists/too-long, granularity range, in_use rules), inline unit tests. Autoclear-bit ordering deferred to Phase 4 (guest). (OQ5 informs P4, OQ6/7/8/10 resolved) | PLAN-bitmap-phase-03-planner.md | Complete (steps 3a-3e; merge landed as pure logic; remove/clear/merge data-cluster I/O split to Phase 4) |
| 4. Guest op (`src/operations/bitmap/`): input-RW op; read config, read header cluster + directory + refcount structures, gate/cross-check, loop the ordered actions through the planner (double-buffered directory + refblocks + AllocCursor), do the on-disk data-cluster work the crate can't (free data clusters on remove/clear, zero new table clusters on add, merge orchestration), write back under the crash-safe clear→write→set autoclear dance, send result; binary-size check. Not functionally testable until Phase 5 — gate is build+size+review (OQ1 store-path/dance, OQ2 merge-or-defer, OQ7 input-device resolved) | PLAN-bitmap-phase-04-guest.md | Complete (steps 4a-4e; all six actions incl. same-image merge; first-add creates the EXT_BITMAPS record; bitmap.bin 37 KiB / 376 KiB. Functional validation deferred to Phase 5/6/7) |
| 5. Host VMM subcommand: `BitmapArgs` clap surface (ordered repeatable action flags via `indices_of`, `-g`/`-f`, `--object`/`--image-opts`/`-b`/`-F` refusal, `--output`), qemu-parity argument validation, host probe (`parse_header_extensions` + autoclear, net-new host-side), `run_bitmap`/`run_bitmap_guest` (input-RW attach like snapshot, growth-capable capacity hint, `BitmapResult` harvest arm), silent/json render + host fsync, error mapping; **first end-to-end smoke test** of Phases 1-4 (OQ2, OQ11) | PLAN-bitmap-phase-05-host-cli.md | Complete (steps 5a-5c; `instar bitmap` fully functional; 50/50 end-to-end smoke test vs qemu-img, zero bugs — the whole Phases 1-4 stack works on first real run) |
| 6. Rust round-trip tests (`src/crates/bitmap/tests/`): a crate-level, public-API, *additive* suite (multi-action sequences + refcount-conservation invariants + empty-bitmap representation) complementing the 71 inline tests — a `common/` fixture toolkit over directory+refblock buffers. Whole-image `qemu-img check` round-trip is Phase 7 (the crate is I/O-free) | PLAN-bitmap-phase-06-rust-tests.md | Complete (steps 6a-6c; tests/ suite: 15 round-trip + 6 sequences + 11 merge, all public-API, refcount-conservation invariants; no crate bug; also cleaned a stray test import) |
| 7. Python integration tests (`tests/test_bitmap.py`, mirroring `test_amend.py`): cross-check vs `qemu-img bitmap` with post-op `qemu-img check` + **qemu-vs-qemu** bitmaps-array equivalence (instar `info` emits no bitmaps), across a cluster-size matrix; the **bits-set same-file merge** validated bit-for-bit via a `qemu-io` seed + `qemu-storage-daemon`/QMP/`qemu-img map` read-back oracle (closes the Phase-5 gap); every refusal path matched to the host messages; `KNOWN_BITMAP_DIVERGENCES` registry | PLAN-bitmap-phase-07-integration.md | Complete (steps 7a-7c; 35 tests: cross-validation matrix across cluster sizes 512/4K/64K/1M, the bits-set same-file merge validated bit-for-bit via the read-back oracle, and 13 refusal contracts matched to the host messages with 4 registered divergences; a cluster-size bug in the guest `stage_refblocks` was found and fixed) |
| 8. Cross-version baselines (spans instar + instar-testdata): `BITMAP_CASES` + `generate_bitmap_baseline` (per-case *op sequence*) + a `baselines-bitmap` target in `generate-baselines.py`, `expected-outputs/bitmap-info-json/qcow2/<version>/` for the 80-version matrix; `normalise_info_json` sorts the bitmaps array; `TestBitmapBaselineMatrix` compares **qemu-img info of instar's output** vs the version-matched stored qemu baseline (so instar `info` emitting no bitmaps is irrelevant); testdata push operator-gated. Modest value (bitmap metadata is version-stable) — parity + future-proofing | PLAN-bitmap-phase-08-baselines.md | Complete (steps 8a-8c; instar side committed on the bitmap branch; instar-testdata `bitmap-baselines` branch has the full 80-version matrix — 640 case runs, 0 failures — committed locally, **operator must push** it; metadata byte-identical across all versions, confirming instar's layout reads consistently across qemu 6.0-10.2) |
| 9. Coverage fuzz — `fuzz_bitmap_parse` (Phase-1 qcow2 bitmap parsers, panic-freedom) + `fuzz_bitmap_planners` (Phase-3 actions/directory/merge, synthesised directory+refblocks+geometry) registered in the nightly (Cargo.toml + coverage-fuzz.yml TARGETS/N_TARGETS + fuzz-tier.sh FAST_TIER); differential `op_bitmap` in `differential-fuzz.py` vs `qemu-img bitmap` (parity-respecting random op sequences, compares info-bitmaps + `qemu-img check`; the mock CallTable already wires send_bitmap_result). Validates the panic-free discipline; a real bug/divergence is fixed in the product | PLAN-bitmap-phase-09-fuzz.md | Complete (steps 9a-9b; both coverage targets clean over 15.9M/11.6M execs — panic-free discipline held; differential op_bitmap clean over 200 iters after steering the one finding — cross-granularity merge, a documented intentional divergence, not a bug; no product change) |
| 10. Docs (docs-only, closes the plan): new `docs/bitmap.md` user guide (mirrors `docs/amend.md`, code-accurate verbatim messages) registered in `docs/index.md`; `### bitmap` in `docs/usage.md`; op bullet in `ARCHITECTURE.md`; ops bullet + structure line in `AGENTS.md`; link list + usage section in `README.md`; `CHANGELOG.md` Added + a Changed note for resize-refuses-bitmaps; a divergence bullet in `docs/resize.md` (verbatim message); master-plan status → Complete. `order.yml` already lists the plan | PLAN-bitmap-phase-10-docs.md | Complete (steps 10a-10c; docs/bitmap.md written + all surrounding docs updated) |

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The management session is reserved for
planning, review, and decision-making. The workflow per step:
**plan** (high effort) → **spawn a sub-agent** with the brief →
**review the actual files** (the summary describes intent, not
necessarily what changed) → **fix or retry** (improve the brief or
upgrade the model) → **commit** once satisfied. Use
`isolation: "worktree"` for risky/experimental steps.

### Planning effort

The master plan is created at **high effort**. Phases 1 (net-new
format parsing), 2 (ABI / ordered-action encoding / core budget),
3 (planner — refcount correctness, allocation, the two store paths,
the autoclear dance), and 9 (differential fuzz oracle) should be
planned at **high effort**; they involve format-spec
interpretation, cross-file refcount reasoning, and
cross-validation correctness. Phases 4–8 and 10 follow
well-established patterns from amend/snapshot and can be planned at
**medium effort** once the briefs front-load the research.

### Step-level guidance

Each phase plan includes a step table:

```
| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a   | medium | sonnet | none     | One-sentence summary of what to do and which files to touch |
| 1b   | high   | opus   | worktree | Why this needs high effort: requires understanding X to do Y |
```

**Effort levels** — high: multiple files, judgment calls,
non-obvious invariants, external spec research. medium: clear
brief, well-defined approach. low: purely mechanical.

**Model choice** — opus for deep reasoning / cross-file
architectural understanding / subtle correctness (the directory +
table + refcount mutation, the autoclear crash-safety ordering,
the ordered-action ABI bridging VMM and guest, the differential
oracle). sonnet for well-briefed implementation. haiku for
mechanical tasks. **When in doubt, skew to the more capable model**
— a failed implementation wastes more than a heavier model costs. A
detailed brief compensates for a lighter model.

**Brief for sub-agent** — write it as if briefing a colleague who
has never seen the codebase: what to change, which files to follow,
what patterns to follow, and the non-obvious constraints (the
384 KiB guest binary cap, the `no_std` requirement of the format
and planner crates, the call-table boundary, the `repr(C)` layout
discipline, the qcow2 `--features create` test gate, refcount
consistency, the autoclear-bit ordering). Front-load the research
the planner already did. For example, instead of "parse the bitmap
directory", write "in `src/crates/qcow2/src/lib.rs`, add
`EXT_BITMAPS: u32 = 0x23852875` and a `parse_bitmaps_extension`
that, given the 24-byte extension data, returns `nb_bitmaps`,
`bitmap_directory_size`, `bitmap_directory_offset` (all big-endian,
offset is cluster-aligned), and a `parse_bitmap_dir_entry` over the
24-byte fixed head (`bitmap_table_offset` u64, `bitmap_table_size`
u32, `flags` u32, `type` u8, `granularity_bits` u8, `name_size`
u16, `extra_data_size` u32) followed by extra_data then a
non-NUL-terminated name, total rounded up to 8 bytes. Reject any
flag bit outside 0/1 (`BME_RESERVED_FLAGS`), any `type != 1`, and
any `extra_data_size != 0`, matching qemu's `check_dir_entry`."

### Management session review checklist

After a sub-agent completes, verify:

- [ ] The files that were supposed to change actually changed
      (read them, don't trust the summary).
- [ ] No unrelated files were modified.
- [ ] `make instar` builds and `make lint` is clean.
- [ ] Guest binaries pass `make check-binary-sizes` (384 KiB), and
      `core.bin` still fits its 72 KiB `.bss`-inclusive budget.
- [ ] `make test-rust` (including `cargo test -p qcow2
      --features create` and the new `bitmap` crate) passes.
- [ ] Relevant `make test-integration` targets pass.
- [ ] `pre-commit run --all-files` passes.
- [ ] The changes match the intent of the brief — semantically,
      not just syntactically.
- [ ] Commit message follows project conventions (Co-Authored-By
      with model, context window, effort level, and other
      settings; `Signed-off-by`; `Prompt:` paragraph).

## Administration and logistics

### Success criteria

We will know this plan has been successfully implemented because
the following statements will be true:

* `instar bitmap --add <v3-qcow2> bm0` creates an enabled,
  persistent, empty bitmap that `qemu-img info` reports identically
  to one created by `qemu-img bitmap --add`, and `qemu-img check`
  reports no leaks/corruption.
* `instar bitmap --remove <img> bm0` frees the bitmap's clusters
  and (if last) clears the autoclear bit, with `qemu-img check`
  clean; `--clear`, `--enable`, `--disable`, and (same-image)
  `--merge` each match the corresponding `qemu-img bitmap`
  invocation under `qemu-img info`/`check`.
* Multiple actions in one invocation apply in command-line order,
  matching qemu.
* `instar bitmap` on a qcow2 v2 image, a non-qcow2 image, a
  duplicate name, a missing name, an out-of-range granularity, or
  `-g` without `--add` / `-b` without `--merge` is refused with a
  clear, qemu-comparable error.
* `make instar` builds and `make lint` is clean.
* Guest binaries pass `make check-binary-sizes` (384 KiB limit);
  the new `bitmap.bin` is registered in
  `scripts/check-binary-sizes.sh`, and `core.bin` still fits its
  72 KiB budget after the call-table addition.
* All Rust unit tests pass (`make test-rust`), including the new
  `bitmap` crate, the qcow2 bitmap-parsing tests, and round-trip
  tests.
* All Python integration tests pass (`make test-integration`),
  including `tests/test_bitmap.py`.
* Coverage-guided fuzzing of the bitmap parser and planner and
  differential fuzzing against `qemu-img bitmap` run clean.
* `pre-commit run --all-files` passes.
* Documentation in `docs/` (`bitmap.md`, `usage.md`) and
  `ARCHITECTURE.md`, `README.md`, `AGENTS.md`, `CHANGELOG.md`,
  `docs/plans/index.md`, `docs/plans/order.yml` are updated.

### Future work

Obvious extensions deferred from v1:

* **`qemu-img bench`.** The last remaining unimplemented qemu-img
  subcommand. It is a live I/O performance harness, not an image
  inspect/mutate operation, and does not fit instar's sandboxed
  "safe `qemu-img` replacement" mission; orchestration platforms
  rarely invoke it. Revisit only if a concrete need appears. With
  `bitmap` done, instar implements every qemu-img subcommand except
  `bench`.
* **Cross-file `--merge -b SOURCE_FILE`** (if deferred per Open
  question 2): reading a bitmap from a second read-only source
  image through a guest input device, OR-ing into the target.
* **Cross-granularity merge (rescaling).** instar v1 refuses a
  `--merge` when the source and destination bitmaps have different
  granularities (`ERROR_INCOMPATIBLE_MERGE`), while `qemu-img
  bitmap` rescales the source bits to the destination granularity
  and accepts. Surfaced by the Phase-9 differential fuzzer
  (`merge_granularity` in `KNOWN_BITMAP_DIFFERENTIAL_DIVERGENCES`);
  a v2 could implement the rescale-on-merge to match qemu. This is
  the refuse-rather-than-guess posture, not a bug.
* **`instar info` bitmap reporting** (if not done in Phase 1):
  full qemu-img-info-equivalent bitmap listing and formatting.
* **`resize` bitmap-preservation** (Open question 4) — **FIXED on
  this branch**, see Defects below. `resize` now refuses images
  with the qcow2 bitmaps autoclear bit set (matching `snapshot`),
  rather than silently dropping them. A future enhancement could
  add true bitmap-aware resizing (preserve + adjust bitmap geometry
  across the size change) instead of refusing, but that is a
  feature well beyond the data-loss fix.
* **`--object`/`--image-opts`** real handling (beyond rejection),
  if a workflow needs it.
* **`core.bin` budget.** Watch the 72 KiB core ceiling; the next
  subcommand after bitmap that adds a call-table sender may force
  another memory-map budget lift (move `OPERATION_LOAD_ADDR`), a
  loader/layout change affecting every guest binary.
* **Batch merge per-entry I/O (scalability).** PR #386 review finding
  #4 (CONSIDER, non-blocking): the guest merge path (`run_merge` /
  `write_back_merge` in `src/operations/bitmap/src/main.rs`) processes
  bitmap table entries one at a time, issuing a read/modify/write per
  entry. This is correct and fine for typical bitmap tables, but scales
  poorly for very large or externally-created bitmap tables with many
  entries. A v2 could batch contiguous entry I/O (coalesce adjacent
  cluster reads/writes) to reduce the number of guest<->host round
  trips. Correctness-neutral; purely a throughput improvement for the
  large-table case.

### Bugs fixed during this work

List any bugs encountered and fixed during development here. At the
start of Phase 1, scan the GitHub issue tracker for any open
qcow2-bitmap / refcount / header-extension issues this work should
resolve or be aware of, and resolve Open question 4 (whether other
ops silently drop bitmaps) — if they do, that is a candidate bug to
fix or document here.

### Defects found during this work

- **`resize` silently drops persistent dirty bitmaps
  (PRE-EXISTING) — FIXED on this branch (commit `5892e1c`).** Found
  while resolving Open question 4 in Phase 1 (step 1d). `resize`
  rebuilds the whole qcow2 header
  cluster via `qcow2::build_header` (`src/crates/qcow2/src/create.rs:329-410`)
  on every path (header-only grow, grow-with-L1, table-relocate,
  shrink). `build_header` writes a fresh v3 header that omits any
  unknown header extension — including the bitmaps extension
  (`0x23852875`) — and zeroes `autoclear_features` (offset 88).
  Resizing an image carrying persistent dirty bitmaps therefore
  discards the bitmaps directory extension and clears the autoclear
  bit, orphaning/leaking the on-disk bitmap clusters. Every other
  in-place mutation op was audited and is safe: `rebase`/`commit`/
  `check --repair` do selective field writes; `snapshot` does
  targeted 12-byte RMW at offset 60 **and** refuses images with the
  autoclear bitmaps bit set (`mutating_feature_gates`,
  `src/operations/snapshot/src/main.rs:454-460`). Cross-version
  `amend` relocates unknown extensions verbatim but zeroes
  autoclear on a v2→v3 upgrade — harmless in practice since bitmaps
  require v3 (a v2 source has none).

  **Fix (commit `5892e1c`):** `resize` now refuses images with the
  bitmaps autoclear bit set, matching `snapshot`. The gate lives in
  the resize planner (`validate_no_bitmaps`, called from
  `compute_grow_query` and `plan_grow`); the guest op threads the
  raw autoclear word into the planner via a new
  `current_autoclear_features` opts/query field, captured *before*
  any device I/O (`HEADER_BUF` is reused as a bounce buffer, so a
  later re-read returns clobbered bytes — a bug the integration test
  caught). New append-only ABI error code
  `ResizeResult::ERROR_BITMAPS_UNSUPPORTED = 14` with a clear host
  message. Covered by resize-crate unit/planner tests, a
  ResizeResult ABI-stability test, and a `test_resize.py`
  integration test that adds a bitmap with `qemu-img`, asserts
  instar refuses, and asserts the bitmap survives with
  `qemu-img check` clean.

### Documentation index maintenance

When this master plan is created, update:

* **`docs/plans/index.md`** — add a row to the *Master plans* table
  (creation date, link to this plan, one-line intent, initial
  status, links to each phase plan as they are written), in
  chronological order.
* **`docs/plans/order.yml`** — add an entry so this master plan
  appears in the docs navigation. Phase files are *not* added to
  `order.yml`.

When all phases are complete, update the status column in
`index.md` to *Complete*.

### Back brief

Before executing any step of this plan, the executing agent should
back brief the operator as to its understanding of the plan and how
the intended work aligns with it.
