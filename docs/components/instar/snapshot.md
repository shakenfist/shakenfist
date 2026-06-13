# `instar snapshot` — manage internal qcow2 snapshots

`instar snapshot` lists, creates, applies, and deletes the
internal snapshots of a qcow2 image. It is the safe, sandboxed
equivalent of `qemu-img snapshot`: list output is byte-identical
to `qemu-img snapshot -l` (modern ≥9.0 layout), and the three
mutating modes produce images that are bit-for-bit identical to
qemu-img's given identical inputs, modulo the documented
freed-cluster and file-tail notes below.

The `snapshot.bin` guest runs in the KVM sandbox. The host opens
the image read-write as input device 0 and dispatches; the guest
does every parse, every refcount mutation, every L1 copy, and
every header rewrite via `write_input_sector(0, ...)`, with
explicit `fsync_input` barriers between write groups. Untrusted
image metadata never touches the host.

## Synopsis

```
instar snapshot [OPTIONS] FILENAME
```

Mode flags (mutually exclusive; omitting all of them defaults to
list, like qemu-img):

```
  -l, --list               List snapshots (read-only, default)
  -c, --create <NAME>      Create a snapshot named NAME; the ID
                           is auto-assigned (max existing ID + 1)
  -a, --apply <SNAPSHOT>   Apply ("goto") a snapshot by ID or name
  -d, --delete <SNAPSHOT>  Delete a snapshot by name
```

Common options:

```
  -f, --format <FMT>       Format hint; must be qcow2 (any other
                           format is refused with qemu's "does not
                           support image snapshots" error)
  -q, --quiet              Accepted for qemu-img compatibility.
                           No visible effect for any snapshot mode
                           under either tool: success is always
                           silent, errors always print.
  -U, --force-share        List-only no-op (instar takes no image
                           locks). Combined with -c/-d/-a it is
                           refused host-side before any file
                           access, matching qemu's substance.
      --output <FORMAT>    human (default) | json. JSON is an
                           instar extension; qemu-img snapshot -l
                           is human-only.
```

`--image-opts` is rejected with a clear error (consistent with
`measure`, `map`, etc.). The full flag surface is reported by
`instar snapshot --help`.

## Matcher semantics: `-d` and `-a` are asymmetric

qemu 10.x resolves the two mutating arguments through
*different* matchers, and instar matches each exactly (see the
snapshot section of [docs/quirks.md](/components/instar/quirks/)):

| Mode | qemu matcher | Semantics |
|------|--------------|-----------|
| `-d` | `bdrv_snapshot_find` | **name only**, first match in table order |
| `-a` | `find_snapshot_by_id_or_name` | one full pass over the table comparing **IDs**, then — only if no ID matched — a second full pass comparing **names** |

The two-full-pass structure means a *later* entry matching by ID
beats an *earlier* entry matching by name. On an image with
`id=1 name="2"` and `id=2 name="x"`, `-a 2` applies the snapshot
with **ID 2** (the one named "x"), while `-d 2` deletes the one
**named** "2"; a pure-ID argument (`-a 1`) works for apply but is
not-found for delete. `instar convert --snapshot` uses the same
ID-then-name resolver as `-a` (it mirrors `qemu-img convert -l`;
see the convert section of [docs/quirks.md](/components/instar/quirks/), including
its bounded 16-entry lookup cap).

## List mode

```
instar snapshot -l image.qcow2
TZ=UTC instar snapshot -l image.qcow2     # deterministic DATE column
instar snapshot --output=json image.qcow2
```

Human output is byte-identical to `qemu-img snapshot -l` in the
modern (qemu ≥ 9.0) layout: `Snapshot list:` prefix (only when at
least one snapshot exists; an empty table prints nothing and
exits 0), the `ID TAG VM_SIZE DATE VM_CLOCK ICOUNT` header, dates
rendered in **local time** (pin `TZ=UTC` for reproducible
output), 4-digit-hour VM clock, `--` for an absent icount, and
ID/TAG columns padded by **byte** length so multibyte UTF-8 names
lay out exactly as qemu's C `printf` does. Names up to the
255-byte on-disk maximum list in full. A hand-crafted zero
`date_sec` renders the Unix epoch, exactly like qemu. The
old (≤ 8.2) column layout is not emitted; the cross-version
baselines record both families. Details and history for each of
these behaviours are in [docs/quirks.md](/components/instar/quirks/).

`--output=json` emits a flat array whose key names mirror qemu's
QMP `SnapshotInfo` (`id`, `name`, `vm-state-size`, `date`,
`vm-clock`, `icount` — `null` when absent), so QMP consumers can
reuse their parsers. The `date` object carries the raw numeric
seconds/nanoseconds, independent of TZ.

## Mutating modes

```
instar snapshot -c before-upgrade image.qcow2
instar snapshot -a before-upgrade image.qcow2
instar snapshot -d before-upgrade image.qcow2
```

All three modes are qcow2 v2/v3 only and run entirely in the
guest. Success is silent (exit 0); failures print to stderr and
exit 1 with the image untouched up to the documented commit
points.

### Feature gates and refusals

The mutating modes share a uniform gate set (list mode works on
all of these; the gates protect the write path):

- `refcount_bits != 16` — refused (`-c`/`-d`/`-a` use the v1
  16-bit-refcount allocator; 16 is the qemu default for v3 and
  the only width v2 uses).
- Compressed clusters (zstd header bit, or any zlib-compressed
  cluster found during the walk) — refused; refcounting a
  compressed extent needs a multi-cluster walk deferred to
  future work.
- Encrypted images, external data files, dirty bitmaps —
  refused (these change refcount semantics or need write paths
  instar does not have yet).
- Dirty / corrupt images — refused; qemu auto-repairs a dirty
  lazy-refcounts image on RW open, instar will not mutate on top
  of untrustworthy refcounts. Run `qemu-img check -r all` first.

### v1 limits

- **16-snapshot cap**: `-c` refuses to create the 17th snapshot.
  The qcow2 spec allows 65536; raising the cap is future work.
- **No refcount-structure growth**: allocation comes only from
  the refblocks already present in the image. When none have a
  free run left, `-c` fails cleanly where qemu-img would grow
  the refcount table (bites at small cluster sizes — see
  [docs/quirks.md](/components/instar/quirks/)).
- **Names**: longer than 255 bytes refused loudly (qemu silently
  truncates); empty names refused on create (qemu accepts), but
  `-d ''` still deletes an empty-named snapshot for parity.
- **Apply after resize refused**: a snapshot whose stored
  `disk_size` differs from the current virtual size makes qemu
  truncate the image inside apply; instar refuses with a
  resize-back workaround message. Likewise a hand-crafted
  snapshot L1 *larger* than the active L1 is refused (a smaller
  one is zero-padded, like qemu).

### Crash safety

Each mutating mode writes back in `fsync`-separated groups with
a single commit point, adopting qemu's ordering (condensed here
from the guest's module docs; the full group-by-group breakdown
lives in
[docs/qcow2/qcow2-snapshots.md](/components/instar/qcow2/qcow2-snapshots/)):

- **create**: data/L2 refcount increments, the L1 copy, and the
  rewritten active L1 first; then the new snapshot table; then
  the 12-byte header write at offset 60 (`nb_snapshots` +
  `snapshots_offset`) — the commit point; then the old table is
  freed.
- **delete**: a read-only precheck before any write; the
  compacted table; the header write (commit point); then the
  decrements, the COPIED-flag refresh, and the surviving L2
  write-backs.
- **apply**: refcount increments; the snapshot's raw L1 over the
  active L1 (commit point); then decrements plus a final-state
  COPIED refresh. Apply touches no timestamps, no snapshot-table
  bytes, and no header bytes.

A crash before a commit point leaves the old state
authoritative with at worst orphaned clusters; a crash after
leaves the new state authoritative with repairable leaks and/or
stale COPIED flags — `qemu-img check -r` repairs either side;
no ordering ever produces a dangling reference.

## Parity with qemu-img

- **List**: byte-identical to `qemu-img snapshot -l` across the
  baselined fixtures and every name shape qemu can create.
- **Mutations**: byte-identical post-op images given
  byte-identical inputs, when the qemu side runs with
  `--image-opts driver=qcow2,file.filename=...,file.discard=ignore`.
  The `discard=ignore` qualifier disables only qemu's
  protocol-level hole punching over freed clusters — instar
  never writes freed clusters at all, so their stale bytes
  remain where qemu's defaults would punch holes. All *live*
  metadata (snapshot table, L1 copies, refcounts, COPIED flags)
  is identical either way and `qemu-img check` is clean.
- **File tail**: instar writes through 64 KiB virtio sectors, so
  the final write can round the file size up to a sector
  boundary where qemu writes at byte granularity; the trailing
  bytes are zero and the structure is identical.
- **Table padding**: on tables allocated into reused dirty
  clusters, qemu leaves stale inter-entry pad bytes where instar
  writes zeros; the padding is dead bytes no parser reads.

Each of these, plus the smaller CLI-surface divergences (mixed
mode flags exit 2 vs qemu's 1; `-U` refusal wording), is
documented with its discovery history in
[docs/quirks.md](/components/instar/quirks/).

## Verification

- **Shell harnesses** (`tools/snapshot-*.sh`, seven scripts, 241
  assertions): live differential verification against the host
  qemu-img — create/delete/apply byte-identity matrices over the
  fixture grid, refusal batteries, and CLI parity. Run them all
  with `make snapshot-harnesses` (requires a built instar and
  `/dev/kvm`); CI runs the same target in the functional-tests
  workflow's `snapshot-harnesses` job.
- **Integration tests** (`tests/test_snapshot.py`, 94 tests):
  the list matrix against cross-version baselines, JSON goldens
  (`tests/golden/snapshot-list/`), mutation round-trips with
  post-op `qemu-img check`, error paths and qcow2-only
  enforcement, and empty-table behaviour.
- **Cross-version baselines**: `qemu-img snapshot -l` captured
  for 80 qemu-img versions (6.0.0 through 10.2.0) over 12
  fixtures in
  `instar-testdata/expected-outputs/snapshot-list-human/`.
- **Coverage-guided fuzzing**: `fuzz_snapshot_parse` (the
  streaming table parser against adversarial qcow2 fragments)
  and `fuzz_snapshot_refcount` (the refcount mutators, COPIED
  walker, allocator, and table round-trip under semantic
  invariants), both in the nightly coverage-fuzz rotation.
- **Differential fuzzing**: `scripts/differential-fuzz.py`'s
  `op_snapshot` runs random create/delete/apply/write chains
  against qemu-img with byte-identity asserted after every chain
  element. Its first runs caught a real multibyte list-padding
  bug and the delete surviving-L2 COPIED gap; both are fixed and
  documented.

## Future work

Tracked under the [PLAN-snapshot master
plan](/components/instar/plans/PLAN-snapshot/)'s Future work section: compressed
clusters, dirty bitmaps, external data files, encrypted images,
the 16-entry cap family (create's 17th-snapshot refusal and
convert `--snapshot`'s bounded lookup; list mode already streams
to the spec cap), disk_size-mismatch apply (qemu's embedded
truncate), refcount-structure growth on create, and
`fsync_input` rollout to commit.

## Examples

List (deterministic dates for diffing against qemu-img):

```
TZ=UTC instar snapshot -l image.qcow2
```

JSON for scripting:

```
instar snapshot -l --output=json image.qcow2
```

Snapshot before a risky change, then roll back:

```
instar snapshot -c pre-change image.qcow2
...
instar snapshot -a pre-change image.qcow2
```

Clean up an old snapshot before transferring an image:

```
instar snapshot -d pre-change image.qcow2
```

Bare filename defaults to list, like qemu-img:

```
instar snapshot image.qcow2
```
