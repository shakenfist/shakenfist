# PLAN-bitmap phase 06: Rust round-trip tests

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read the actual
code and ground every claim in it — in particular the Phase-3
`bitmap` crate (`src/crates/bitmap/src/{lib,directory,action,merge}.rs`)
and its extensive inline tests, the Phase-1 primitives in
`src/crates/qcow2/src/bitmap.rs`, the reusable refcount accessors in
`src/crates/snapshot/src/qcow2.rs`, and the amend `tests/` suite
(`src/crates/amend/tests/qcow2_round_trip.rs` + `tests/common/`)
which is the structural template. Do not speculate when you could
read. Flag any uncertainty explicitly.

Phase plans live in `docs/plans/` as
`PLAN-bitmap-phase-NN-<descriptive>.md`. The master plan is
[PLAN-bitmap.md](/components/instar/plans/PLAN-bitmap/); Phases 1–5 are complete —
`instar bitmap` runs end-to-end and passed a 50/50 smoke test
against `qemu-img`. This is the sixth of ten phases. One commit per
logical change, minimum one per phase.

## Situation

This phase adds a **crate-level `tests/` round-trip suite** for
`src/crates/bitmap/`. It is a **tests-only phase** — no library,
guest, or host changes.

**Scope honesty (read this first).** The `bitmap` crate already has
**71 thorough inline unit tests** (directory 31, action 25, merge
13, lib 2), and the Phase-5 smoke test validated the *whole stack*
end-to-end against `qemu-img`. So this phase is deliberately
**additive, not duplicative**. It covers what the inline tests and
smoke test do *not*:

- **Public-API-only** exercise (the `tests/` crate sees only `pub`
  items — a contract check that the public surface is
  complete/usable).
- **Multi-action sequences** — the guest applies an *ordered list*
  of actions, double-buffering the directory (Phase-4 `run_actions`).
  The inline tests mostly exercise single actions; this suite drives
  sequences (`add;disable`, `add(a);add(b);remove(a)`, `add;clear`,
  `enable;disable`, …) through the public API and asserts the final
  state.
- **Refcount-conservation invariants** — the property that makes
  `qemu-img check` pass: after each action, allocated
  table/data/directory clusters have refcount ≥ 1, freed clusters
  are 0, and nothing is double-allocated. This suite checks the
  crate-observable slice of that invariant explicitly after every
  step.
- **The empty-bitmap representation** (master OQ8) as a named
  round-trip: `add` ⇒ zero-filled table, no data clusters; `clear`
  returns to it.
- A **reusable fixture builder** (`tests/common/`) that Phase 9
  (fuzzing) can also consume.

**What this phase does NOT do (and why):** it does *not* build a
whole qcow2 image and re-run `qemu-img check` — unlike amend's
crate (which emits a replayable `AmendPlan`), the `bitmap` crate is
an **in-place slice mutator** over directory + refcount-block
buffers and does *not* own the on-disk orchestration (directory
placement, cluster writes, extension updates, the autoclear dance —
all Phase-4 guest work). So the true whole-image round-trip
(post-op `qemu-img info`/`check`, byte-parity vs `qemu-img bitmap`)
lives in **Phase 7** (integration tests through the host CLI + the
`qemu-img` oracle). This phase validates the crate's directory +
refcount mutations in isolation.

Grounding (verified against HEAD `e4101d4`):

- **amend `tests/` template**: `src/crates/amend/tests/common/mod.rs`
  holds fixture builders + an `apply_amend` that replays the plan
  into a `Vec<u8>`; `tests/qcow2_round_trip.rs` (`mod common;`, 14
  `#[test]`s) does exact-byte + parsed-invariant round-trips.
  bitmap mirrors the *structure* (a `tests/common/mod.rs` +
  `tests/*.rs`) but its fixtures are **directory + refblock byte
  buffers**, not whole images.
- **`bitmap` crate public API** the suite drives: `action::{Bitmap
  Geometry, ActionOutcome, action_add/remove/clear/enable/disable}`,
  `directory::{FoundBitmap, find_bitmap, directory_byte_len, entry_
  bounds, build_directory_*, serialize_bitmaps_extension}`,
  `merge::{MergeSpec, merge_validate, merge_cluster_action,
  MergeClusterAction, or_bitmap_data}`, and `BitmapError`.
- **Fixture + assertion primitives** (all `pub`): from
  `qcow2::bitmap` — `BitmapDirEntry` (+ `zeroed()`),
  `serialize_bitmap_dir_entry`, `parse_bitmap_dir_entry`,
  `for_each_bitmap_entry` (streaming re-parse — but it needs the
  `CallTable` reader; for tests prefer walking with
  `parse_bitmap_dir_entry` directly, or the pure
  `for_each_bitmap_entry_with` core if exposed — check), the table
  codec, geometry helpers, and the `BME_*` constants; from
  `snapshot::qcow2` — `read_refcount_in_block` / `set_refcount_in_
  block` / `AllocCursor` (to build refblock fixtures and assert
  refcounts).
- **`[dev-dependencies]`**: `src/crates/bitmap/Cargo.toml` already
  lists `shared`, `qcow2`, `snapshot` under `[dev-dependencies]`
  (added in 3a). Confirm they suffice for the `tests/` crate (they
  should — no `create` feature needed, since we build directory +
  refblock buffers by hand, not whole images).
- **Test invocation**: the `bitmap` crate runs under `make
  test-rust`'s workspace pass (no Makefile change needed — confirmed
  in 3e). `std` is available in `tests/` (integration tests are
  their own std crate).

## Mission and problem statement

Add `src/crates/bitmap/tests/` such that:

1. **`tests/common/mod.rs`** provides a reusable fixture + assertion
   toolkit (all via the crate's public API):
   - `Fixture` — a small struct carrying the directory buffer
     (`Vec<u8>` + `nb_bitmaps`), the refblock buffer (`Vec<u8>`),
     and a `BitmapGeometry`, modelling the state the Phase-4 guest
     stages.
   - `build_geometry(cluster_size, virtual_size, num_refblocks,
     occupied_clusters)` — a `BitmapGeometry` with `refcount_bits =
     16`, `host_refblocks_start = 0` (refblock index 0 ⇒ cluster 0),
     `refblock_count = num_refblocks`.
   - `build_refblocks(cluster_size, num_refblocks, occupied_clusters)`
     — a refblock buffer with clusters `0..occupied` at refcount 1
     (image metadata) and the rest free, via `set_refcount_in_block`.
     Choose `occupied` so the refcount structure itself is
     accounted and there are free clusters for the allocator.
   - `build_directory(entries: &[(name, granularity_bits, enabled,
     in_use)])` — serialize the given entries (via
     `serialize_bitmap_dir_entry`) into a packed directory buffer +
     the byte length; each entry needs a plausible
     `bitmap_table_offset`/`bitmap_table_size` (point them at
     distinct allocated clusters recorded in the refblocks so
     remove/clear can free them).
   - `parse_entries(dir, nb_bitmaps) -> Vec<ParsedEntry>` — re-parse
     the directory (walk with `parse_bitmap_dir_entry`) into a
     comparable form (name, granularity_bits, flags/enabled/in_use,
     table_offset, table_size).
   - `refcount_at(refblocks, geom, host_offset) -> u64` — via
     `read_refcount_in_block` + the cluster→refblock mapping (mirror
     the crate's private `refblock_byte_range_for_cluster`).
   - `apply(fixture, action)` helpers that call the right
     `action_*` with a fresh out-buffer and swap it in (the
     double-buffer the guest does), returning the `ActionOutcome`.

2. **`tests/round_trip.rs`** — single-action public-API round-trips:
   for each of add/remove/clear/enable/disable, apply to a fixture,
   re-parse, and assert the directory + refcount effects and the
   empty-bitmap representation. (These overlap the inline tests in
   spirit but validate through the public surface and pin the
   invariants as named round-trips.)

3. **`tests/sequences.rs`** — multi-action sequences applied like
   the guest's ordered loop (double-buffered), asserting the final
   directory state and a **refcount-conservation check after each
   step** (Open question 3): e.g. `add(a) → disable(a)` ⇒ one
   enabled-then-disabled entry, one table cluster still allocated;
   `add(a) → add(b) → remove(a)` ⇒ only `b` remains, `a`'s table
   cluster freed; `add(a) → clear(a)` ⇒ `a` present, data freed,
   table zeroed; `enable`/`disable` idempotency.

4. **Merge** (`tests/merge.rs` or folded into `round_trip.rs`) —
   `merge_validate` outcomes (both present / dest-missing /
   source-missing / in_use / granularity-mismatch / self-merge),
   the `merge_cluster_action` truth table, and `or_bitmap_data`,
   exercised through the public API. (Largely covered inline; keep
   this light — a public-API smoke of the merge surface.)

5. The suite passes under `make test-rust`; `make lint` and
   `pre-commit run --all-files` are clean. A round-trip that
   surfaces a real crate bug is **fixed in the crate** (with a
   phase-3 inline test), not papered over by weakening the
   assertion.

**Out of scope:** whole-qcow2-image round-trips + `qemu-img`
oracle (Phase 7); baselines (Phase 8); fuzzing (Phase 9); docs
(Phase 10). No library/guest/host changes.

## Open questions

### 1. Fixture realism — directory+refblock buffers, not whole images

Confirmed: the crate is I/O-free and operates on directory +
refblock slices, so the fixtures are those slices (plus geometry),
not a `create`-built qcow2 image. This is the honest ceiling of a
crate-level test; the whole-image round-trip is Phase 7. Confirm no
one expects a `qemu-img check` here.

### 2. Building a refblock fixture the allocator can use

`action_add`/merge allocate via `snapshot::qcow2::alloc_contiguous_
clusters_in_refblocks`, which first-fit-scans the staged refblocks
for refcount-0 clusters from the `AllocCursor`. The fixture must:
mark the image's own metadata clusters (header cluster 0, the
refcount table, the refblock clusters themselves, any pre-existing
bitmap table clusters) as refcount 1, and leave a contiguous run of
free clusters after them. Decide the exact toy layout (e.g.
cluster_size 64 KiB, 1 refblock covering the first N clusters, first
~8 clusters occupied, the rest free) and document it in
`common/mod.rs`. Keep it minimal but self-consistent so
`refcount_at` assertions are meaningful.

### 3. What refcount-conservation can a crate-level test actually assert?

Full "no leaks / no corruption" needs walking every qcow2 structure
(that's `qemu-img check`, Phase 7). At the crate level, assert the
*observable* slice: after `add`, the newly-allocated table
cluster(s) are refcount 1 and no other refcounts changed; after
`remove`, the freed table clusters are refcount 0 and the entry is
gone; after `clear`, data clusters (that the guest would free) are
signalled via `ActionOutcome.freed_table_offset` and the entry
stays; a sequence's net refcount delta equals the sum of per-action
deltas. Define a `assert_refcounts(fixture, expected: &[(offset,
count)])` helper and a "no unexpected changes" diff against a
snapshot of the refblocks taken before the action.

### 4. Merge data-cluster caveat

The crate's merge functions (`merge_cluster_action`,
`or_bitmap_data`) are pure and fully inline-tested; the *data
orchestration* (reading/OR-ing/allocating data clusters) is Phase-4
guest code, untestable here. Keep `tests/merge.rs` to the pure
public surface; the real merge round-trip (with bits set) is a
Phase-7 integration test (the Phase-5 smoke test already deferred
the bits-set case there).

## Step-level guidance

Plan at **medium** effort — well-trodden test scaffolding following
the amend template; the crate is already heavily inline-tested, so
this is additive breadth, not deep new logic. sonnet with a
detailed brief.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | medium | sonnet | none | Create `src/crates/bitmap/tests/common/mod.rs` (the fixture toolkit, Mission §1) — study `src/crates/amend/tests/common/mod.rs` for structure, but build **directory + refblock byte buffers** (not whole images). Define the toy refcount layout (Open question 2) and document it. Then `tests/round_trip.rs` (`mod common;`) with the single-action public-API round-trips (Mission §2) + the empty-bitmap representation. Confirm `src/crates/bitmap/Cargo.toml` `[dev-dependencies]` suffice (add `snapshot`/`qcow2`/`shared` if missing; no `create` feature). Validate with `make test-rust` (the new tests run under the workspace pass) — do NOT run cargo directly (sandbox-denied); use the dockerized `make` targets. `make lint` clean. |
| 6b | medium | sonnet | none | `tests/sequences.rs` (Mission §3) — multi-action double-buffered sequences with the `assert_refcounts` / refblock-diff conservation check after each step (Open question 3); and `tests/merge.rs` (Mission §4) — the pure merge public surface. If a sequence exposes a real crate bug, STOP and report it (fix in `src/crates/bitmap/src/*.rs` with a phase-3 inline test, don't weaken the assertion). Validate with `make test-rust` + `make lint`. |
| 6c | low | sonnet | none | Mark the phase-6 row complete in `docs/plans/PLAN-bitmap.md` + `index.md`. Full gate: `make instar` (expect `bitmap.bin`/`core.bin` unchanged — tests-only), `make test-rust`, `make lint`, `make check-binary-sizes`, `pre-commit run --all-files`. Present the commit(s). |

## Management session review checklist

- [ ] `tests/common/mod.rs` uses only the crate's **public API**;
      the toy refcount layout is documented and self-consistent.
- [ ] Round-trip + sequence tests assert the directory effects,
      the empty-bitmap representation, and the refcount-conservation
      slice after each step.
- [ ] Tests are genuinely **additive** (sequences + invariants +
      public-API), not a re-paste of inline unit tests.
- [ ] Any crate bug a test surfaces is **fixed in the crate**, not
      hidden by a weak assertion.
- [ ] No library/guest/host files changed; `bitmap.bin`/`core.bin`
      unchanged.
- [ ] `make test-rust`, `make lint`, `make check-binary-sizes`,
      `pre-commit run --all-files` green.
- [ ] Commit messages follow conventions.

## Success criteria

- `src/crates/bitmap/tests/` exists with a `common/` fixture
  toolkit + round-trip + sequence (+ merge) suites, all through the
  public API, passing under `make test-rust`.
- The suite adds real coverage: ordered multi-action sequences and
  refcount-conservation invariants the inline tests don't check,
  plus the empty-bitmap representation as a named round-trip.
- No production code changed (or, if a bug was found, a minimal
  crate fix + inline regression test, reported to the management
  session).
- All gates green.

## Back brief

Before executing any step, the executing agent should back brief
the operator — in particular that this is a **crate-level,
public-API, additive** suite (sequences + refcount invariants,
*not* a re-paste of the 71 inline tests), that fixtures are
**directory + refblock byte buffers** (the crate is I/O-free — the
whole-image `qemu-img check` round-trip is Phase 7), and that any
real bug found is **fixed in the crate**, not asserted around.
