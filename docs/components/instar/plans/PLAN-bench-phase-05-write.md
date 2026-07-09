# PLAN-bench phase 05: write tests

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (the commit guest op's
allocate-on-write composition, the snapshot crate's allocator and
refcount mutators, bitmap's RW-input staging idiom, the bench op
and host CLI as they stand after phase 4), and ground your
answers in what the code actually does today. The master plan's
**"Findings: allocating-write reuse for `-w` on qcow2 (OQ4, step
1d)"** section is the authoritative reuse map — read it in full;
every file:line reference below comes from it or from the
phase-3/4 surveys. Do not speculate when you could read instead;
flag uncertainty explicitly.

Phase plans live alongside the master plan
([PLAN-bench.md](/components/instar/plans/PLAN-bench/)) in `docs/plans/`. This is the
fifth of eight phases; phases 1-4 landed the schedule crate, the
ABI, the guest read op, and the host CLI (read benchmarks work
end-to-end on all five formats with header byte-parity).

I prefer one commit per logical change, and at minimum one commit
per phase. Each commit should be self-contained: it should build,
pass tests, and have a clear commit message explaining what
changed and why.

## Situation

This phase makes `-w` real for **raw and qcow2**:
`--pattern`/`--flush-interval`/`--no-drain` semantics, the
RW-input attach with `fsync_input` flush wiring (OQ5, resolved in
phase 2), allocating qcow2 writes per the OQ4 reuse verdict, and
refusals for vmdk/vhd/vhdx write tests. qemu's verified write
behaviour (master plan): ordinary format-layer writes filling the
buffer with the pattern byte — and because qemu's per-slot iovecs
are dead code (every in-flight request writes the same
`qiov[0]`), **qemu never writes request-distinguishable data
either**, so after identical `-w` runs the instar and qemu images
are byte-comparable. Phase 6 exploits that.

Grounding:

- **The reuse map (OQ4/1d, verified then):** the commit guest op
  (`src/operations/commit/src/main.rs:711-785`) is the working
  allocate-on-write template (allocate+zero L2 when the L1 slot
  is empty, allocate a data cluster when the L2 slot is empty,
  write data, set both `OFLAG_COPIED` flags), with metadata flush
  ordering at `:787-834`. The pure primitives live in
  `crates/snapshot` (`alloc_contiguous_clusters_in_refblocks` +
  `AllocCursor` `src/crates/snapshot/src/qcow2.rs:299-435`,
  refcount RMW accessors `:51/:152/:239`, COPIED rewriters
  `:450-532`) and `crates/commit` (L2/refcount disk-offset math
  `src/crates/commit/src/qcow2.rs:296-321`). bitmap
  (`src/operations/bitmap/src/main.rs`) is the RW-input-slot-0 +
  `fsync_input(0)` precedent. Commit's `write_output_byte_range`
  (`src/operations/commit/src/main.rs:175`) is the sub-sector RMW
  idiom (read covering sectors, patch, write back) — bench needs
  the *input-device* analog.
- **Net-new pieces the findings enumerated:** the per-offset
  driver; the overwrite-in-place fast path (allocated + COPIED ⇒
  data write only); staging the target's own refcount
  table/blocks (commit stages the backing's — directly analogous
  host pre-read plumbing); sub-cluster fill-then-patch for
  freshly allocated clusters; and the snapshot gate.
- **The write envelope** (union of the sister mutators, plus one):
  `refcount_bits == 16` only; no compression, no extended-L2, no
  external data file, no LUKS, not dirty/corrupt; **and `nb_snapshots
  > 0` refused** — commit blind-overwrites without a COPIED check
  because it assumes unique ownership; bench must not corrupt
  snapshot-shared clusters. Allocation never grows the refcount
  table (`RefcountExhausted` → clean refusal; one 16-bit refblock
  at 64 KiB clusters covers 2 GiB of address space, so this bites
  rarely and is a documented caveat).
- **Phase 3/4 state:** the guest op refuses `FLAG_WRITE` with
  `ERROR_BAD_CONFIG` (placeholder branch, commented); the host
  refuses `-w` in `validate_bench_args` (`bench: write tests (-w)
  are not yet supported`); the flush line renderer and
  `flushes-issued` JSON field are already wired;
  `flush_after_completion`/`total_flushes` sit unit-tested in
  `crates/bench`; the read path attaches the whole chain
  read-only. `fsync_input(u32)` (call-table slot, VERSION 17)
  fdatasyncs an RW input slot; the host stub returns false for
  RO slots.
- **The read dispatch stays available in write mode** —
  `read_chain_virtual_range`/`read_chain_virtual_cluster` over
  the same `ChainStates` — which is what makes backing-chain COW
  cheap (below).

## Mission

### 1. Semantics: what a bench write is

For each scheduled offset, write `bufsize` bytes of the pattern
byte at that virtual offset through the format layer, exactly as
a guest OS write would land:

- **raw**: patch `[offset, offset+bufsize)` in the flat file.
  With the 65536-byte transport sector, arbitrary offsets need
  sub-sector RMW: read the covering sectors, patch the window,
  write back (a `write_input_byte_range` helper mirroring
  commit's `write_output_byte_range`, targeting RW input slot 0).
- **qcow2**: per touched cluster (a request may straddle a
  cluster boundary — split at cluster granularity like the read
  path does):
  - *Overwrite fast path*: L2 entry allocated + `OFLAG_COPIED` ⇒
    patch the data cluster in place (sub-sector RMW against the
    host cluster offset). No metadata changes.
  - *Allocating path*: allocate a data cluster
    (`alloc_contiguous_clusters_in_refblocks` over the staged
    refblocks); **fill it with the cluster's current virtual
    content via the chain read** (`read_chain_virtual_cluster`
    at this device with the entry still unallocated — falls
    through to the backing chain, or yields zeros when there is
    none: one uniform rule that makes overlay COW correct *and*
    covers the zero-fill fresh-cluster case); patch the pattern
    window; write the full data cluster; update the L2 entry
    (host offset + COPIED); if the L1 slot had no L2 table,
    allocate + zero one first and update the L1 entry (+COPIED).
  - Writes through the backing chain never touch parent images:
    all allocation and data lands in the top image only; parents
    stay attached read-only.

### 2. Metadata write-back architecture (the crash-ordering decision)

**Decision: write-through L2/L1, staged refcounts, refcounts
last.**

- Data cluster writes go straight to the file (virtio RW input).
- L2 (and L1) updates are **written through** immediately after
  the data they point to — the update is a covering-sector RMW of
  the table cluster on disk. Ordering per update: data cluster
  first, then the L2 entry that makes it reachable, then (only
  when a new L2 was allocated) the L1 entry. No fsync between —
  matching qemu's promise level, which orders metadata through
  its cache without syncing mid-run.
- Refcount updates accumulate in **staged refblocks** (the
  bitmap/snapshot staging idiom, host-preloaded like commit
  pre-reads the backing's), written back at every flush point and
  once after the loop. Refcounts-last means a crash mid-bench
  leaves *leaked clusters* (allocated data reachable via L2 but
  under-refcounted → `qemu-img check` reports leaks, repairable),
  never a dangling L2 pointer — the same benign artifact class a
  qemu crash mid-bench produces.

Why not full staging of L2s: the request schedule can scatter
75000 requests across arbitrarily many L2 tables (large step over
a large image), so any staged-L2 budget is a refusal waiting to
happen; write-through has no cap and per-update ordering falls
out naturally. Why not write-through refcounts: they are the
high-frequency RMW target (every allocation), staging them is
bounded (`REFBLOCKS_LIMIT`-style, exactly how snapshot/bitmap
bound theirs), and deferring them is what makes the
crash-artifact benign. The 5b implementer must diff this order
against commit's proven `:787-834` sequence and justify any
deviation in the review.

**Flush points** (`flush_after_completion` says flush after
completion k): write back dirty staged refblocks, then
`fsync_input(0)`, count it in `flushes_issued`. `--no-drain` is
an accepted no-op (serial execution: the queue is always drained)
— still issue the flush. After the loop (interval 0 or not): a
final refblock write-back (no fsync unless the cadence issued
one at k == count) **before** `send_bench_result` — the result
closes the timing bracket, so end-of-run metadata write-back is
inside the measured window. This is a documented measurement
caveat: qemu amortises metadata through its cache during the
run; instar concentrates refcount write-back at flush points and
run end. The bracket placement contract itself is unchanged.

### 3. Gates and errors

Guest-side (qcow2 header is parsed there), checked before
`send_bench_start` — all pre-bracket, result-without-marker:

- The envelope: `refcount_bits != 16`, compression feature/any
  compressed cluster encountered (entry-level check during the
  run as well — hitting a compressed L2 entry in write mode is
  `ERROR_WRITE_UNSUPPORTED` mid-run), extended-L2, external data
  file, LUKS, dirty/corrupt incompatible bits, `nb_snapshots >
  0`. New `BenchResult` codes (appended, stable): 7
  `ERROR_WRITE_UNSUPPORTED` (`error_detail` = a small gate-id
  enum documented in shared), 8 `ERROR_ALLOC_EXHAUSTED`
  (`RefcountExhausted` — host renders "image too large for
  in-place bench write" per the findings).
- Host-side: `-w` with discovered format ∉ {raw, qcow2} →
  `bench: write tests are not yet supported for <fmt>`
  (divergence registry). The phase-4 `-w` refusal is removed;
  `validate_bench_args`'s cross-option rules (flush-requires-
  write, interval ≥ depth) now become reachable end-to-end.
- Host attach for `-w`: top image RW input slot 0, parents (if
  any) RO — a mixed-mode variant of the chain attach (commit
  already opens an RW input alongside other devices; mirror its
  open flags). Read tests keep the all-RO attach. `fsync_input`
  requires the RW slot, so the host stub's RO-slot `false` is
  the guard that the wiring is right.

### 4. What phase 5 does NOT do

No vmdk/vhd/vhdx writes (refused, Future work). No COW-granular
subclusters (extended-L2 is gated out). No refcount-table growth.
No new call-table slots, no VERSION bump (`fsync_input` and the
existing senders suffice — OQ5 as resolved in phase 2). No
guest-side "self-check" pass: post-write verification is
host-side in 5c/phase 6 (`qemu-img check` clean, pattern
read-back, qcow2 disk-size growth), where it is cross-validated
against qemu rather than against ourselves.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | high | opus | none | Raw write path end-to-end: guest `write_input_byte_range` (sub-sector RMW on RW input slot 0, mirroring commit's output-side helper), the FLAG_WRITE branch for raw (per-offset pattern writes + `flush_after_completion` cadence + `fsync_input(0)` + `flushes_issued`), host `-w` enablement (drop the 4a refusal, RW-top attach, FLAG_WRITE + pattern into BenchConfig, vmdk/vhd/vhdx refusal message), flush-line rendering now reachable. Smoke: `-w --pattern 65 -c 100` on a raw image — pattern verifiable via cmp/xxd, `--flush-interval` count matches `total_flushes`, fsyncs actually reach the file (strace or /proc counters), byte-compare against a qemu `-w` run with identical args. Commit 1. |
| 5b | high | opus | worktree | qcow2 allocating writes per Mission §1-§3: the per-cluster driver (overwrite fast path; allocating path with chain-read COW fill), write-through L2/L1 ordering, staged refblocks + flush-point/end write-back, the full gate set + the two new error codes + host rendering. MUST diff the resulting write-back order against commit's `:787-834` and state the comparison explicitly in the report; MUST verify the COW fill against a qemu `-w` run on an overlay — the oracle is the **virtual view** (`qemu-img compare` full-image equality) plus `qemu-img check` clean on ours; physical byte-identity of the two overlays is NOT required (allocator placement may legitimately differ) but record whether it holds anyway. Worktree isolation: this is in-place-mutation code on the most corruption-prone path. Gates: build/size/lint/test-rust plus its own smoke (fresh qcow2 grows, check clean, pattern lands, overlay COW correct). Commit 2. |
| 5c | medium | sonnet | none | The recorded verification sweep + bookkeeping: matrix over {raw, fresh qcow2, populated qcow2, overlay} × {-w defaults, --pattern 65, --flush-interval 50 -d 1, --no-drain} — for each: instar vs qemu image byte-compare (or qemu-img compare where physical layouts legitimately differ — record which), `qemu-img check` clean, disk-size growth on fresh qcow2, flushes-issued == total_flushes, gate refusals (snapshot-bearing image, refcount_bits=1, compressed image, vmdk/vhd/vhdx), `--output json` fields. Append `## Captured write verification (step 5c)` to this plan; master plan row → Complete; index update; pre-commit. Stop-and-report on any content mismatch. Commit 3. |

## Captured write verification (step 5c)

Verified against local `qemu-img 10.0.8` (Debian 1:10.0.8+ds-0+deb13u1+b2)
on `instar` built from `2a3d3af` (`make instar`, binary at
`src/target/release/instar`). All work done in a scratch directory outside
the repo; every run used a fresh pristine copy of the relevant base image
(one copy for instar, one for qemu-img, never shared). No stop-and-report
events fired: zero content mismatches, zero `check` errors/leaks, zero
refusals that touched an image, zero stdout parity breaks.

### Base images

- **raw**: 10 MiB flat file, zeroed except an MBR `55 AA` signature at
  bytes 510-511.
- **fresh qcow2**: `qemu-img create -f qcow2 fresh.qcow2 64M` (no data
  written).
- **populated qcow2**: same 64 MiB qcow2, then
  `qemu-io -c "write -P 0xbb 0 8M"` (first 8 MiB written).
- **overlay**: a `backing.qcow2` identical to the populated image (64 MiB,
  first 8 MiB filled with `0xbb`), with a qcow2 overlay pointed at it via
  `-b`/`-F qcow2` (absolute backing path so a copy in any directory still
  resolves — instar's default `SecurityConfig` restricts backing references
  to the top image's own directory, so the shared backing file was copied
  into the working directory used for the sweep).

### Argument sets

- (a) `-w -c 100 --pattern 65 -f <fmt>` (pattern `0x41`, no flush, `-d`
  defaulted to 64)
- (b) `-w -c 100 --pattern 65 --flush-interval 50 -d 1 -f <fmt>`
- (c) `-w -c 100 --pattern 65 --no-drain --flush-interval 50 -d 1 -f <fmt>`
- (d) `-w -c 200 -s 131072 -o 32768 --pattern 66 -f <fmt>` (pattern `0x42`,
  128 KiB buffer over a 64 KiB cluster — every request straddles two
  clusters; offset 32768 is mid-cluster, so the first touched cluster's
  low half is outside the patch window)

### Matrix verdicts (16 runs)

All exit codes 0 except the one documented divergence below (raw × (d),
qemu-img side). Stdout header lines byte-identical between instar and
qemu-img on every run that completed; the "Sending flush every 50 requests"
line is present on both sides for (b) and (c) and absent from both for (a)
and (d).

| Image | Argset | Exit (instar/qemu) | Content oracle | `check` | Disk-size growth (instar → qemu) |
|-------|--------|---------------------|-----------------|---------|-----------------------------------|
| raw | a | 0 / 0 | `cmp` byte-identical | n/a (raw) | fixed size, no growth (10485760 → 10485760 both sides) |
| raw | b | 0 / 0 | `cmp` byte-identical | n/a | no growth |
| raw | c | 0 / 0 | `cmp` byte-identical | n/a | no growth |
| raw | d | 0 / **1** | not comparable — see divergence note below | n/a | no growth (file stays 10485760; instar wrote the full wrapped schedule in place) |
| fresh qcow2 | a | 0 / 0 | `qemu-img compare`: identical | clean, no leaks | grows: 200704 → 786432 (instar) / 200704 → 729088 (qemu) |
| fresh qcow2 | b | 0 / 0 | `qemu-img compare`: identical | clean | grows: 200704 → 786432 / 729088 |
| fresh qcow2 | c | 0 / 0 | `qemu-img compare`: identical | clean | grows: 200704 → 786432 / 729088 |
| fresh qcow2 | d | 0 / 0 | `qemu-img compare`: identical | clean | grows: 200704 → 26607616 / 26550272 |
| populated qcow2 | a | 0 / 0 | `qemu-img compare`: identical (physical bytes also identical — fast overwrite path) | clean | no growth: 8716288 → 8716288 both sides |
| populated qcow2 | b | 0 / 0 | identical, physical bytes identical | clean | no growth |
| populated qcow2 | c | 0 / 0 | identical, physical bytes identical | clean | no growth |
| populated qcow2 | d | 0 / 0 | `qemu-img compare`: identical (physical bytes differ — (d)'s range runs past the pre-populated 8 MiB, so it takes the allocating path there) | clean | grows: 8716288 → 26607616 / 26611712 (expected — (d) writes well past the 8 MiB pre-populated region) |
| overlay | a | 0 / 0 | `qemu-img compare`: identical | clean | grows: 200704 → 786432 / 724992 |
| overlay | b | 0 / 0 | identical | clean | grows: 200704 → 786432 / 724992 |
| overlay | c | 0 / 0 | identical | clean | grows: 200704 → 786432 / 724992 |
| overlay | d | 0 / 0 | identical | clean | grows: 200704 → 26607616 / 26550272 |

Physical-byte match (informative, not required): identical on `raw` (all
formats there are physically byte-comparable by construction) and on
`populated` for (a)/(b)/(c) (both instar and qemu take the fast
overwrite-in-place path over already-allocated clusters, so allocator
placement doesn't enter it). Physical bytes differ on every `fresh`,
`overlay`, and `populated`-(d) row — expected, since instar's and qemu's
cluster allocators legitimately place new clusters differently; the virtual
view (`qemu-img compare`) is the required oracle there and matched on every
row.

**raw × (d) divergence (not a stop-and-report event):** `qemu-img bench`
10.0.8 fails partway through this row with `Failed request: Input/output
error` (byte-count analysis: it completes ~19 of 200 requests, writing
2,543,616 bytes of pattern `0x42`, before hitting its own EOF-overrun bug).
This is the pre-existing qemu 10.0.8 wrap-rule bug that `crates/bench`
already documents and deliberately does not reproduce (master plan OQ7,
`src/crates/bench/src/lib.rs` wrap-rule doc comment): a 10 MiB raw image
with a 128 KiB buffer stepping from offset 32768 for 200 requests overruns
EOF under the naive "advance and let it fail" rule 10.0.8 still ships, and
qemu-img bails with an I/O error instead of wrapping. instar adopts the
fixed **master** wrap rule and completes all 200 requests, wrapping every
overrunning offset back into `[0, image_size)`: the resulting file is
10,354,688 bytes of pattern `0x42` out of 10,485,760 total (the remainder
untouched — the wrap modulus leaves a small unwritten remainder, plus the
2-byte MBR signature which a wrapped write happened not to cover), file
size unchanged at 10,485,760 throughout. Because the two tools genuinely
diverge in behaviour here (one succeeds, one errors), a `cmp` between the
two output files is not a meaningful oracle for this row; instar's own
output was independently verified (pattern-byte census, no unexpected
values, file size unchanged) and is correct per its documented wrap
contract. This is the write-path analogue of the read-path OQ7 finding
recorded in phase 1 — worth folding into `KNOWN_BENCH_DIVERGENCES` in
phase 6/8.

### `--output json` flush-count verification

Ran `--output json` on every image with two argument sets: one with no
flush interval (expect `flushes-issued` 0) and one with
`--flush-interval 50` at `-c 100` (expect `flushes-issued` 2, matching
`crates/bench::total_flushes(100, 50) == 2`). All eight runs matched
exactly:

| Image | flush-interval 0 | flush-interval 50, count 100 |
|-------|-------------------|-------------------------------|
| raw | `flushes-issued: 0` | `flushes-issued: 2` |
| fresh qcow2 | `flushes-issued: 0` | `flushes-issued: 2` |
| populated qcow2 | `flushes-issued: 0` | `flushes-issued: 2` |
| overlay | `flushes-issued: 0` | `flushes-issued: 2` |

### Refusal rows (instar only; image untouched, confirmed by sha256)

| Case | Refused with | sha256 unchanged |
|------|---------------|-------------------|
| qcow2 with internal snapshot (`qemu-img snapshot -c snap1`) | `bench: write tests are not supported for this image (internal snapshots)` | yes |
| `refcount_bits=1` qcow2 | `bench: write tests are not supported for this image (refcount_bits != 16)` | yes |
| Compressed qcow2 (verified via `qemu-img check`: 100% compressed clusters after `qemu-img convert -c` of compressible data) | `bench: write tests are not supported for this image (compression)` | yes |
| vmdk (`qemu-img convert -O vmdk`) | `bench: write tests are not yet supported for vmdk` | yes |
| vhdx (`qemu-img convert -O vhdx`) | `bench: write tests are not yet supported for vhdx` | yes |
| vhd (`qemu-img convert -O vpc -o subformat=dynamic`; instar's discovered format name is `vpc`) | `bench: write tests are not yet supported for vpc` | yes |
| LUKS-encrypted qcow2 (`-o encrypt.format=luks,encrypt.key-secret=...`) — not skipped, quick to build | `bench: write tests are not supported for this image (encryption)` | yes |

Regression guard: `--flush-interval 50` without `-w` on a plain raw image
still produces the qemu cross-option message verbatim —
`--flush-interval is only available in write tests` — byte-identical to
`qemu-img bench --flush-interval 50 -f raw` on the same image, exit 1,
image untouched (sha256 unchanged).

### Overlay COW spot-proof

Repeated on the pristine matrix overlay (not a throwaway copy) after run
(d): request 0 of (d) writes bytes `[32768, 163840)`, which starts
mid-way through cluster 0 (`[0, 65536)`, cluster size 65536). Byte 20000 —
inside the touched cluster, outside the patched window — reads `0xbb`
(matching the backing file's content at the same offset) via
`qemu-io -c "read -v 20000 16" overlay.qcow2`; byte 32768 (start of the
patch window) reads `0x42` (pattern 66). This confirms the allocating
path's chain-read COW fill preserved the rest of the newly-allocated
cluster's virtual content exactly, patching only the intended window.

### Measurement observations (informative only, no assertions)

Elapsed times (host `Instant` bracket) were consistently higher for
instar than for qemu-img across every row, most pronounced on the
allocating/(d) rows:

| Image | Argset | instar elapsed | qemu elapsed |
|-------|--------|-----------------|---------------|
| raw | a | 0.007s | 0.001s |
| raw | b/c | 0.019s | 0.010-0.011s |
| raw | d | 0.037s | n/a (errored) |
| fresh qcow2 | a | 0.017s | 0.012s |
| fresh qcow2 | b/c | 0.024s | 0.019-0.020s |
| fresh qcow2 | d | 0.137s | 0.024s |
| populated qcow2 | a | 0.017s | 0.001s |
| populated qcow2 | b/c | 0.029s | 0.012s |
| populated qcow2 | d | 0.116s | 0.012s |
| overlay | a | 0.015s | 0.012s |
| overlay | b/c | 0.025s | 0.017-0.018s |
| overlay | d | 0.145s | 0.022s |

The gap widens most on (d) (cluster-straddling, allocating writes), which
is consistent with Mission §2's documented measurement caveat: instar
concentrates staged-refblock write-back at flush points and at run end,
inside the measured bracket, whereas qemu amortises metadata writes
through its page cache during the run. No assertion is made about
acceptable overhead magnitude — this is the sandbox-overhead data point
the master plan's announcement-email motivation calls for, left for
phase 6/8 to interpret.
