# instar bench — benchmark the sandboxed I/O path

`instar bench` issues a scripted sequence of read or write requests
against a disk image and reports how long they took — the sandboxed
equivalent of `qemu-img bench`.

**Read this before trusting a number.** `instar bench` does **not**
measure what `qemu-img bench` measures. `qemu-img bench` times qemu's
block layer running over the host page cache. `instar bench` times
**instar's own end-to-end sandboxed I/O path**: the guest format layer
(qcow2/vmdk/vhd/vhdx/raw parsing) → the virtio-block device → the
host's ioeventfd dispatch → the host I/O thread → the underlying file
I/O. These are different stacks doing different amounts of work, and
their absolute numbers are not comparable to each other. What *is*
useful and reproducible: running both tools against the **same
image** with the **same arguments** and comparing the two numbers —
that comparison isolates the sandbox's own overhead, and is the
methodology this document is built around (see "Sandbox-overhead
methodology" below).

## Synopsis

```
instar bench [-c COUNT] [-d DEPTH] [-s BUFFER_SIZE] [-S STEP_SIZE]
             [-o OFFSET] [-w] [--pattern NUM] [--flush-interval NUM]
             [--no-drain] [-t CACHE] [-i AIO] [-n] [-f FMT] [-q] [-U]
             [--image-opts] [--output {human,json}] FILENAME
```

Options:

```
  -c, --count <COUNT>        Number of requests to issue. Default 75000.
                             Range [1, 2147483647].
  -d, --depth <DEPTH>        Queue depth. Default 64. Range
                             [1, 2147483647]. Echoed for output parity;
                             execution is serial in v1 (see below).
  -s, --buffer-size <SIZE>   Bytes per request (suffixes accepted:
                             b/k/K/m/M/g/G/t/T/p/P/e/E). Default 4096.
                             Range [1, 2147483647], further capped at
                             2 MiB by instar.
  -S, --step-size <SIZE>     Byte offset advance per request (same
                             suffixes as -s). Default 0, meaning "use
                             the buffer size". Range [0, 2147483647].
  -o, --offset <SIZE>        The first request's byte offset (same
                             suffixes as -s). Default 0.
                             Range [0, 9223372036854775807] (i64::MAX).
  -w                         Run a write test instead of a read test.
                             Destructive -- see "Write tests" below.
      --pattern <NUM>        Byte pattern used to fill write buffers.
                             Default 0. Range [0, 255]. Silently
                             ignored (not an error) on read tests.
      --flush-interval <NUM> Issue a flush every N completed requests.
                             Default 0 (never). Range [0, 2147483647].
                             Requires -w when nonzero; must be >= depth.
      --no-drain             Accepted no-op -- v1's serial execution
                             always drains the (single-entry) queue.
  -t, --cache <CACHE>        Cache mode. Only 'writeback' (qemu's
                             default) is supported; other valid qemu
                             modes are refused as not-yet-supported.
  -i, --aio <AIO>            AIO backend selector. Every value is
                             refused; no backend is implemented yet.
  -n                         Native AIO. Refused; not yet supported.
  -f, --format <FMT>         Format hint. Ignored -- the input format
                             is auto-detected, like dd's -f posture.
  -q                         Suppress the progress indicator. instar
                             has none; accepted as a no-op.
  -U, --force-share          Force sharing. instar performs no image
                             locking; accepted as a no-op.
      --image-opts           Indicates FILENAME is a complete image
                             specification. Not yet supported.
      --output <FORMAT>      human (default) | json
```

`FILENAME` is the image to benchmark. The full flag surface is
reported by `instar bench --help`.

## What the number means

The timing bracket is a **host wall-clock measurement**: it starts
when the guest emits a start marker and ends when the guest emits the
result message. The guest emits the start marker only after
**config validation, the format probe/open, cached-state setup, and
buffer/transfer-plan setup are complete** — immediately before
submitting the first request, mirroring `qemu-img`'s own
`gettimeofday` bracket placement. Guest boot, image open, and table
loads all happen before the marker and are excluded from the
measured window.

Within that bracket, four caveats govern what the resulting number
actually represents:

- **`-d` is echoed, not obeyed.** The depth value is validated and
  reported in the header and in `--output json`'s `depth` field for
  output parity with `qemu-img`, but v1's guest driver is
  **synchronous and single-buffer**: every request is submitted,
  completed, and only then is the next one submitted. `--output
  json`'s `effective-depth` is always `1`, regardless of `-d`. A
  bench run at `-d 64` does not exercise 64-deep queuing; it
  exercises serialized, one-at-a-time I/O.
- **The virtio transport moves data in 64 KiB sectors.** A request
  whose offset or length is not aligned to that sector size works
  through the **covering sectors**: reads fetch them and discard the
  excess, writes perform a sub-sector **read-modify-write**. A single
  bench "request" of `-s` bytes may therefore correspond to several
  virtio round trips, not one.
- **Write-test metadata decisions happen inside the bracket.** For
  qcow2, each touched cluster is classified against a staged metadata
  window that loads L1/L2 tables from disk as the schedule advances,
  and all metadata write-back (L2, L1 and refcounts, refcounts last)
  is concentrated at flush points and again at the end of the run —
  all of that work is inside the timed window. `qemu-img` instead
  amortises its metadata through an in-memory cache across the whole
  run. This means write timings are **not like-for-like at fine
  grain** between the two tools, even on identical arguments.
- **Only two comparisons are meaningful.** Numbers are comparable
  across **different instar versions**, on the same host and image
  (the perf-regression use case). Numbers are comparable
  **instar-vs-qemu on the same invocation, same image** — that
  comparison is the sandbox-overhead measurement described next.
  Nothing else is comparable: not instar-vs-qemu across different
  arguments, not instar-vs-any-other-tool, and not the absolute
  number in isolation.

## Sandbox-overhead methodology

Because `instar bench` and `qemu-img bench` measure genuinely
different stacks, the useful signal is not either number alone but
the **ratio** between them on an identical invocation. A worked
example:

```
$ qemu-img create -f qcow2 probe.qcow2 256M
$ for i in 1 2 3 4 5; do
    qemu-img bench -f qcow2 -c 20000 -s 4096 probe.qcow2
  done
$ for i in 1 2 3 4 5; do
    instar bench -f qcow2 -c 20000 -s 4096 probe.qcow2
  done
```

Take the median completion time from each set of five runs and
compare them (`instar_median / qemu_median`). Repeating several times
and comparing medians (rather than a single run) smooths out
scheduler jitter — both tools' single-run timings vary run to run on
a shared, busy host.

Which of the four caveats above apply depends on whether the
invocation is a read or a write test:

- **Read tests** (no `-w`): the serialized-depth caveat and the
  64 KiB transport-granularity caveat both apply. The metadata-in-
  bracket caveat does not — nothing is written.
- **Write tests** (`-w`): all four caveats apply, and the
  metadata-in-bracket caveat dominates on allocating writes (fresh
  clusters, cluster-straddling requests) — the sandbox-overhead gap
  is measurably wider there than on simple overwrite-in-place writes,
  because instar's refcount write-back happens inside the bracket
  where qemu's does not.

Use `-f` explicitly on both sides of the comparison (as in the
example above) — an unspecified format on a raw image makes
`qemu-img` print an unrelated auto-detection warning to stderr that
has nothing to do with the measurement.

## Write tests (`-w`)

**`-w` is destructive.** It mutates the target image **in place**,
with **no confirmation prompt**, exactly like `qemu-img bench -w`.
Point it only at a scratch image or a copy you are willing to lose.

### The v1 write envelope

Write tests are supported on **raw** and **qcow2** images (including
qcow2 overlays with a backing chain) only. Any other discovered
format — vmdk, vhd, vhdx — is refused before the guest launches (see
Refusals and errors).

A qcow2 image must additionally pass every one of these gates, or the
guest refuses the write test:

| Gate | Refuses when |
|---|---|
| `refcount_bits != 16` | The image does not use 16-bit refcounts. |
| Compression | The image has the compression incompatible-feature bit set (or any unrecognised incompatible-feature bit). |
| Extended L2 | The image uses the extended-L2 (subcluster) feature. |
| External data file | The image stores data in an external file. |
| Encryption | The image is LUKS-encrypted (`crypt_method != 0`). |
| Dirty / corrupt | The image's dirty or corrupt incompatible bits are set. |

**Internal snapshots are now supported (copy-on-write).** Before
the PLAN-q workcow2-write-infrastructure, `bench -w` refused any
image with `nb_snapshots > 0` because its overwrite path had no
per-write ownership check. Now (step 7d) the qcow2 `-w`
path runs the crate's copy-on-write branch: a snapshot-shared data
cluster is copied before it is written (copy `D → D'`, repoint the
L2, `rc(D')=1`, `rc(D)`−1) and a snapshot-shared L2 table is COWed,
so the pre-existing snapshots are **preserved** (like commit) and the
active view stays `qemu-img compare`-identical to a qemu twin with
`qemu-img check` clean. The internal-snapshots write-gate no longer
fires; its gate id is kept for defensive mapping only.

Allocation never grows the refcount structures **during** the run.
Instead, setup computes the schedule's worst-case allocation bound
and preemptively grows them once, before the timing bracket opens:
new refblocks are placed at the current end of the host file, and if
the refcount table itself is out of slots it is relocated to the file
end, enlarged, and committed with an fsync-ordered header flip. The
old table is then freed through the normal staged-refcount cadence —
a crash between the header flip and the next refcount write-back
leaves the old table's clusters as a repairable leak (reported and
repaired cleanly by `qemu-img check`), the same benign artifact class
as any mid-bench crash. A schedule whose worst case already fits the
populated refblock coverage performs no growth and no extra writes.

The refusal `bench: image too large for in-place bench write`
survives only for schedules that exceed the staging budget: more than
2048 refblock slots, more than 2 MiB of staged refblock bytes, or a
grown refcount table larger than 64 KiB. That is roughly a 256 MiB
host file at 512-byte clusters, and 64 GiB or more at cluster sizes
of 64 KiB and up.

The qcow2 `-w` path runs on the shared `crates/qcow2-write`
planner and `crates/qcow2-write-exec` executor — the same
allocate-on-write infrastructure commit and rebase use, with
bench as its third consumer (the PLAN-qcow2-write-infrastructure work). The planner classifies each scheduled write from a
staged metadata window and emits the mutation steps; the
executor runs them through the call table. Refcount growth (see
above) stays a bench-specific setup step, but now calls the
growth planner that moved into `crates/qcow2-write` and routes
its I/O through the shared executor's byte-range layer. The
shared write path — the step-program ABI, the envelope, the
allocate-on-write and copy-on-write emission, refcount growth
and the crash-ordering contract — is documented in the
[qcow2 write planner and executor reference](/components/instar/qcow2/qcow2-write-planner/).

### Write-back design

For each scheduled offset, bench writes `bufsize` bytes of the
`--pattern` byte at that virtual offset through the format layer. For
qcow2, an already-allocated, already-`OFLAG_COPIED` cluster is
patched in place (a sub-sector read-modify-write, no metadata
change); an unallocated cluster is allocated (a fresh L2 table first
when the L1 slot is empty, then the data cluster), filled with its
current virtual content (read through the backing chain, or
zero-filled if there is none), patched, and its L1/L2 entries are
updated. On a backed image a partial allocating write reads the
pre-image through the chain and resubmits the full cluster, so the
backing is honoured (the crate refuses a bare partial allocating
write with a backing file; bench does the chain fill itself).

Metadata is **staged and written back in dependency order at each
flush epoch, refcounts last**: within an epoch a data cluster
reaches disk before the L2 entry that makes it reachable, a fresh
L2 table before the L1 entry that reaches it, and refblocks after
all of those. A flush epoch runs at each `--flush-interval` flush
point and once more after the run completes. This ordering means a
crash mid-bench leaves, at worst, **leaked clusters** (allocated and
reachable via L2, but under-counted in the refcount table — reported
and repaired cleanly by `qemu-img check`), and **never** a dangling
L2 pointer into unallocated space. This is the same benign artifact
class a `qemu-img bench -w` crash produces.

**Durability / fsync posture.** bench's image is input slot 0
opened read-write, so `fsync_input(0)` genuinely syncs. The
executor's own flushes are disabled; instead bench issues exactly
one `fsync_input(0)` per `--flush-interval` cadence point, after
that epoch's write-back, and none at end-of-bracket or for
`--flush-interval 0`. The `--output json` `flushes-issued` field
counts those cadence points only — never the 1-2 fsyncs setup-time
refcount growth issues. This preserves bench's pre-migration fsync
count exactly.

### Overlay COW correctness

Writes against a qcow2 overlay never touch the parent image: all
allocation and all data lands in the top image only, and every parent
in the chain stays attached read-only. When an allocating write needs
to fill a newly-allocated cluster's untouched bytes, it reads the
cluster's current virtual content through the backing chain (falling
through to the parent, or to zero if there is no parent) before
patching the pattern window — this single rule both makes overlay COW
correct and covers the zero-fill case for a genuinely fresh image.

## Refusals and errors

All failure messages exit `1`. Host-side checks run before the guest
launches; guest-side checks are mapped from the guest's structured
result code. Messages below are verbatim from the source
(`validate_bench_args`, `run_bench`, and `map_bench_error` in
`src/vmm/src/main.rs`).

### Host-side (rejected before the guest runs)

Each of the seven numeric options has two failure forms: an
**unparseable** value produces the value-echoing form
(`Invalid <name> specified: '<value>'.`); an out-of-range *number*
produces the `Must be between` form. These checks run in the order
listed.

| Condition | Message |
|---|---|
| `-c` unparseable | `Invalid request count specified: '<v>'.` |
| `-c` < 1 or > 2147483647 | `Invalid request count specified. Must be between 1 and 2147483647.` |
| `-d` unparseable | `Invalid queue depth specified: '<v>'.` |
| `-d` < 1 or > 2147483647 | `Invalid queue depth specified. Must be between 1 and 2147483647.` |
| `-s` unparseable | `Invalid buffer size specified: '<v>'.` |
| `-s` < 1 or > 2147483647 (qemu's bound fires first) | `Invalid buffer size specified. Must be between 1 and 2147483647.` |
| `-s` inside qemu's range but > 2 MiB | `bench: buffer sizes above 2 MiB are not yet supported` |
| `-S` unparseable | `Invalid step size specified: '<v>'.` |
| `-S` > 2147483647 (0 is valid) | `Invalid step size specified. Must be between 0 and 2147483647.` |
| `-o` unparseable | `Invalid offset specified: '<v>'.` |
| `-o` negative or > i64::MAX | `Invalid offset specified. Must be between 0 and 9223372036854775807.` |
| `--pattern` unparseable | `Invalid pattern byte specified: '<v>'.` |
| `--pattern` outside [0, 255] | `Invalid pattern byte specified. Must be between 0 and 255.` |
| `--flush-interval` unparseable | `Invalid flush interval specified: '<v>'.` |
| `--flush-interval` outside [0, 2147483647] | `Invalid flush interval specified. Must be between 0 and 2147483647.` |
| Nonzero flush interval without `-w` | `--flush-interval is only available in write tests` |
| Nonzero flush interval < depth | `Flush interval can't be smaller than depth` |
| `-t` a valid-but-unsupported qemu cache mode (`none`, `writethrough`, `directsync`, `unsafe`) | `bench: cache mode '<v>' is not yet supported` |
| `-t` anything else unrecognised | `Invalid cache mode` |
| `-i` any value | `bench: aio backend '<v>' is not yet supported` |
| `-n` | `bench: native AIO (-n) is not yet supported` |
| `--image-opts` together with `-f`/`--format` | `--image-opts and --format are mutually exclusive` |
| `--image-opts` alone | `bench: --image-opts is not yet supported` |
| Filename count != 1 | `Expecting one image file name` + second line `Try 'instar bench --help' for more info` |
| `-w` against a discovered format other than raw/qcow2 | `bench: write tests are not yet supported for <fmt>` |
| Backing chain discovery failure (e.g. a zero-byte image) | `error discovering backing chain for <file>: <detail>` |

### Guest-side (mapped from the structured result code)

| Condition | Message |
|---|---|
| I/O read failure | `Failed request: Input/output error` (bare, qemu-parity text) |
| Guest rejected the configuration | `bench: guest rejected the configuration` |
| Unsupported input format | `bench: unsupported input format` |
| Image parse failed | `bench: failed to parse the image` |
| I/O write failure | `bench: writing back to the image failed` |
| Flush failure | `bench: flushing the image failed` |
| Write test refused by the write-envelope gate | `bench: write tests are not supported for this image (<reason>)` (see the write-gate table below) |
| qcow2 allocation exhausted | `bench: image too large for in-place bench write` |
| Image metadata inconsistent or an unsupported entry shape (`crates/qcow2-write` classification refusal — unknown/reserved L1 or L2 bit patterns, refcount inconsistency or coverage gap, staged-regions mismatch; a v3 zero-flag on the target L2 entry) | `bench: image metadata is inconsistent` |
| No result received at all | `bench: guest did not return a result` |
| Result arrived with no preceding start marker | `bench: guest sent a result without a start marker` |

### Write-gate reasons

`<reason>` in the `ERROR_WRITE_UNSUPPORTED` message above comes from
a small gate-id enum carried in the guest result's `error_detail`
field:

| Gate id | Reason text | Triggered by |
|---|---|---|
| 0 | `format not supported` | A non-{raw, qcow2} format reaching the guest under `-w` (defence in depth; the host already refuses this before launch). |
| 1 | `refcount_bits != 16` | The qcow2 image does not use 16-bit refcounts. |
| 2 | `compression` | Compression incompatible-feature bit set (or an unrecognised incompatible-feature bit); also raised mid-run if a compressed L2 entry is encountered. |
| 3 | `extended L2` | The qcow2 image uses extended L2 (subclusters). |
| 4 | `external data file` | The qcow2 image stores data in an external file. |
| 5 | `encryption` | The qcow2 image is LUKS-encrypted. |
| 6 | `dirty or corrupt` | The qcow2 dirty or corrupt incompatible bits are set. |
| 7 | `internal snapshots` | Retained for defensive mapping only. Since copy-on-write was adopted, `bench -w` COWs snapshot-shared clusters instead of refusing, so neither the `nb_snapshots > 0` host check nor a mid-run snapshot-shared cluster raises this gate. |
| anything else | `unsupported feature` | Reserved for future gate ids. |

## `--output json`

`--output json` **replaces** the three human-readable lines entirely
— the default (human) path is byte-parity with `qemu-img`, and
`json` is a wholly separate rendering, not an addition to it.

| Key | Type | Notes |
|---|---|---|
| `filename` | string | The benchmarked image path, as given. |
| `format` | string | The discovered top-of-chain format. |
| `count` | integer | `-c`, as validated. |
| `depth` | integer | `-d`, as validated and echoed — not obeyed (see "What the number means"). |
| `effective-depth` | integer | Always `1`: v1 executes serially regardless of `-d`. |
| `buffer-size` | integer | `-s`, in bytes. |
| `step-size` | integer | The **effective** step: `-S`'s value, or `buffer-size` when `-S` was `0`/unset. |
| `offset` | integer | `-o`, in bytes. |
| `write` | boolean | Whether this was a write test. |
| `pattern` | integer | `--pattern`'s byte value (0-255). |
| `flush-interval` | integer | `--flush-interval`, as validated. |
| `no-drain` | boolean | `--no-drain`'s presence. |
| `flushes-issued` | integer | The number of flushes the guest actually issued. |
| `elapsed-seconds` | number | The host timing bracket, at microsecond precision (six decimal places) — finer than the human line's three-decimal `%.3f` rounding. |
| `requests-per-second` | number | Derived: `count / elapsed-seconds`. |
| `bytes-per-second` | number | Derived: `count * buffer-size / elapsed-seconds`. |

The derived rate fields are computed once and included directly
(rather than left for a consumer to recompute) so a downstream
perf-tracking job never has to reproduce the division and risk
rounding drift against the value instar itself reports.

## Known divergences from `qemu-img bench`

Each entry below is recorded in the `KNOWN_BENCH_DIVERGENCES`
registry in [`tests/test_bench.py`](https://github.com/shakenfist/instar/blob/develop/tests/test_bench.py), named
by its registry key, so a cross-validation mismatch that is *not*
registered there is treated as a real regression.

- **`depth-serialized`.** `-d` is echoed in the header for output
 parity, but bench executes serially in v1; `--output json` always
 reports `"effective-depth": 1` regardless of the requested depth.
- **`wrap-rule-10-0-8`.** instar adopts qemu master's fixed wrap rule
 (`offset %= image_size - bufsize`) so a wrapped request never
 overruns EOF; qemu-img 10.0.8 still ships the older `% image_size`
 rule and can hit `Failed request: Input/output error` near EOF on
 a request that wraps past the last full buffer. Observed on both
 the read path (a 10240-byte image,) and the write path
 (a cluster-straddling schedule wrapping past a 10 MiB raw image,
).
- **`cache-modes-refused`.** `-t` with any cache mode other than the
 default `writeback` is refused (`bench: cache mode '<v>' is not
 yet supported`); `qemu-img` runs every valid cache mode (e.g.
 `-t none`) successfully.
- **`aio-refused`.** `-i <anything>` is refused (`bench: aio backend
 '<v>' is not yet supported`); `qemu-img` runs with any recognised
 aio backend.
- **`native-aio-refused`.** `-n` is refused (`bench: native AIO (-n)
 is not yet supported`); `qemu-img` also fails for `-n` alone, but
 for an unrelated host requirement (`aio=native... requires
 cache.direct=on`) — both fail, for different reasons, not a
 "qemu succeeds" divergence.
- **`image-opts-refused`.** `--image-opts` alone is refused
 (`bench: --image-opts is not yet supported`); `qemu-img` runs it.
 (`--image-opts` combined with `-f`/`--format` is qemu parity: both
 refuse with the same mutual-exclusion text.)
- **`bufsize-cap-2mib`.** `-s` values inside qemu's
 `[1, 2147483647]` range but above 2 MiB are refused (`bench:
 buffer sizes above 2 MiB are not yet supported`); `qemu-img` runs
 them (confirmed live with `-s 3M`).
- **`help-hint-names-instar`.** The filename-count error's second
 line names instar, not qemu-img: `Try 'instar bench --help' for
 more info` vs qemu's `Try 'qemu-img bench --help' for more info`.
- **`zero-byte-early-failure`.** instar fails during backing-chain
 discovery for a zero-byte image, before any header ever prints
 (`error discovering backing chain for <file>:...`); qemu-img
 prints the header unconditionally then fails the first request
 (`Failed request: Input/output error`). Both exit 1, but the
 failure point and text are unrelated.
- **`write-formats-limited`.** `-w` is refused on vmdk/vhd/vhdx
 (`bench: write tests are not yet supported for <fmt>`); `qemu-img`
 writes all of them. instar v1 only supports write tests on raw
 and qcow2.
- **`secure-raw-detection`.** A headerless raw image (no MBR or
 other recognisable signature) is refused as an unsupported format
 — instar's security posture requires a recognisable signature
 before a file is treated as raw; `qemu-img` benches it happily.

## Examples

Default read test (defaults: 75000 requests, 4 KiB each, depth 64):

```
$ instar bench -f raw disk.raw
Sending 75000 read requests, 4096 bytes each, 64 in parallel (starting at offset 0, step size 4096)
Run completed in 2.841 seconds.
```

A smaller, illustrative run:

```
$ instar bench -c 100 -f raw disk.raw
Sending 100 read requests, 4096 bytes each, 64 in parallel (starting at offset 0, step size 4096)
Run completed in 0.004 seconds.
```

The Ceph `cli_migration.sh` write invocation
(`qemu-img bench -f qcow2 -w -c 65536 -d 16 --pattern 65 -s 4096`),
adapted to instar. `-d 16` is accepted and echoed but every request
still runs serially (see "What the number means") — instar exercises
the same request *sequence* as the Ceph script, not the same queuing
behaviour:

```
$ instar bench -w -c 65536 -d 16 --pattern 65 -s 4096 -f qcow2 disk.qcow2
Sending 65536 write requests, 4096 bytes each, 16 in parallel (starting at offset 0, step size 4096)
Run completed in 11.247 seconds.
```

`--flush-interval` on a write test (the "Sending flush every..." line
only appears when the interval is nonzero):

```
$ instar bench -w -c 100 --pattern 65 --flush-interval 50 -d 1 -f raw disk.raw
Sending 100 write requests, 4096 bytes each, 1 in parallel (starting at offset 0, step size 4096)
Sending flush every 50 requests
Run completed in 0.016 seconds.
```

`--output json`:

```
$ instar bench -c 100 -f raw --output json disk.raw
{
    "filename": "disk.raw",
    "format": "raw",
    "count": 100,
    "depth": 64,
    "effective-depth": 1,
    "buffer-size": 4096,
    "step-size": 4096,
    "offset": 0,
    "write": false,
    "pattern": 0,
    "flush-interval": 0,
    "no-drain": false,
    "flushes-issued": 0,
    "elapsed-seconds": 0.005576,
    "requests-per-second": 17934.38,
    "bytes-per-second": 73459203.96
}
```

Refused — a validation error (live transcript):

```
$ instar bench -c 0 -f raw disk.raw
Error: "Invalid request count specified. Must be between 1 and 2147483647."
```

A write test against a qcow2 image with an internal snapshot now
**succeeds** (copy-on-write, now):

```
$ instar bench -w -c 10 -f qcow2 disk.qcow2
Sending 10 write requests, 4096 bytes each, 64 in parallel (starting at offset 0, step size 4096)
Run completed in 0.012 seconds.
```

Refused — a write-gate error on a qcow2 image that uses the
extended-L2 (subcluster) feature (live transcript; note the header
still prints before the guest-side gate fires):

```
$ instar bench -w -c 10 -f qcow2 disk.qcow2
Sending 10 write requests, 4096 bytes each, 64 in parallel (starting at offset 0, step size 4096)
Error: "bench: write tests are not supported for this image (extended L2)"
```

## Future work

- **True queue depth.** The guest driver is synchronous
  single-buffer in v1; a real in-flight-request queue would make
  `-d` do something, at the cost of a materially more complex guest
  driver.
- **TSC-based per-request latencies.** The current bracket reports
  one aggregate elapsed time; per-request latency histograms would
  need a cheap in-guest clock source.
- **`-t none` / O_DIRECT.** Bypass the host page cache, matching
  qemu's `-t none` cache mode, instead of refusing it.
- **vmdk/vhd/vhdx writes.** Extend the write envelope beyond
  raw/qcow2 once each format's allocating-write path exists.
- **A CI perf-tracking job.** Consume `--output json`'s derived
  rates to catch performance regressions across instar versions —
  the reason the JSON schema includes precomputed rates today.
- **Buffers above 2 MiB.** Lift `BENCH_MAX_BUFSIZE` once a larger
  scratch/staging budget is available.
- **An explicit host-path measurement mode.** A mode that measures
  the host's own I/O path directly (bypassing the sandbox) would let
  the sandbox-overhead ratio be computed without a second qemu-img
  invocation.

For the request-schedule crate, see
[`src/crates/bench/`](https://github.com/shakenfist/instar/tree/develop/src/crates/bench); the refcount-growth
planner now lives in the `growth` module of
[`src/crates/qcow2-write/`](https://github.com/shakenfist/instar/tree/develop/src/crates/qcow2-write), which also
provides the qcow2 `-w` allocate-on-write planner (executed through
[`src/crates/qcow2-write-exec/`](https://github.com/shakenfist/instar/tree/develop/src/crates/qcow2-write-exec)).
For the guest operation, see
[`src/operations/bench/`](https://github.com/shakenfist/instar/tree/develop/src/operations/bench). For the
divergence registry, see `KNOWN_BENCH_DIVERGENCES` at the top of
[`tests/test_bench.py`](https://github.com/shakenfist/instar/blob/develop/tests/test_bench.py). See also
[usage.md](/components/instar/usage/). For the qcow2-write migration quirks, see
[quirks.md](/components/instar/quirks/).
