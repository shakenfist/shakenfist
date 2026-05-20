# PLAN-create phase 3: host VMM subcommand

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on external concepts (QCOW2, VMDK, VHD/VHDX, KVM, virtio,
disk image formats, qemu-img semantics), research as needed to
give a confident answer. Flag any uncertainty explicitly rather
than guessing.

This is a phase plan under `PLAN-create.md`. Refer to that master
plan for overall context, mission, and the multi-phase plan
structure. Phase 1 (`PLAN-create-phase-01-emitters.md`) shipped
the metadata-emitter library; phase 2
(`PLAN-create-phase-02-guest-op.md`) shipped the guest binary.
Phase 3 makes the new subcommand reachable from the `instar`
CLI for the first time.

## Mission

Add `instar create` as a real CLI subcommand: a `Commands::Create
(CreateArgs)` clap variant plus a `run_create()` function in
`src/vmm/src/main.rs` that:

1. Parses the user's arguments (target format, output filename,
   virtual size, optional backing reference, per-format option
   flags, output format, quiet mode).
2. For `-f raw` with no preallocation, short-circuits to host-side
   `open(O_CREAT|O_TRUNC|O_RDWR)` + `ftruncate(SIZE)`. No guest
   launch.
3. For every other target format, opens the output file, attaches
   it as the output virtio-block device, optionally opens a backing
   file read-only and attaches it as input device 0, populates a
   `CreateConfig`, launches the create guest binary, waits for the
   `CreateResultMessage`, and renders the result to the user.

Phase 3's goal is the **minimum viable end-to-end** subcommand —
enough that `instar create -f qcow2 foo.qcow2 1G` produces a
working empty qcow2 image. The full `-o key=value` parser lands in
phase 4; richer backing-file plumbing (path resolution edge
cases, vhdx-as-backing) lands in phase 5; preallocation modes land
in phase 6. The integration test suite is a smoke set here;
phase 8 owns the full matrix.

## What the survey turned up

### VMM scaffolding (`src/vmm/src/main.rs`)

- `parse_memory_size(s: &str) -> Result<u64, _>` (line 307)
  parses qemu-img-style SIZE strings (`1G`, `512M`, etc.) into
  bytes. Already supports the suffixes we need; the K/M/G/T fix
  from measure phase 7b means `1T` works.
- `get_binary_path("create.bin")` (line 1521) auto-discovers
  guest binaries beside the instar executable.
- `load_guest_binary(path)` loads the flat binary into memory.
- `create_guest_memory(GUEST_MEM_SIZE)` allocates the guest's
  physical memory region.
- `BackingStore::open(path, read_only, capacity_hint, sparse)`
  (line 2048 etc.) opens a host file as a virtio backing store.
  For create's output: `read_only=false`, capacity hint from the
  resolved virtual size, sparse=true (matches convert).
- `VirtioBlockDevice::new(backing, mmio_base, vq_base, ...)`
  builds the device; `device_set.add_device(dev, is_input)`
  registers it (`is_input=false` for the output).
- `device_mmio_base(index)` / `device_vq_base(index)` compute
  MMIO addresses by device index.

The closest precedent is `run_convert` (line 4559, ~1080 lines)
for the "attach output device + input device + launch guest" flow.
`run_measure` (line 5636, ~330 lines) is the precedent for the
shorter "attach one input device + launch guest + render
result" flow.

### Existing helpers we can reuse

- `parse_memory_size` for the SIZE positional.
- `MAX_SECTOR_SIZE`, `MEASURE_RESULT_MAGIC` patterns for sector
  validation and result-magic touches.
- `discover_backing_chain` exists for input chains, but phase 3
  does not need chain composition — backing is a single immediate
  parent.

### CreateConfig layout (phase 2)

`src/shared/src/lib.rs::CreateConfig` (added in step 2a) — the
host populates every field then writes the struct to
`OPERATION_CONFIG_ADDR`. Phase 3 fills it from `CreateArgs`.

Notable field semantics:
- `virtual_size == 0` ⇒ the guest infers from backing. The host
  should set it explicitly when the user provided `SIZE`, and
  leave it zero when the user provided only `-b BACKING`.
- `flags`: bit set for `FLAG_EXTENDED_L2`, `FLAG_LAZY_REFCOUNTS`,
  `FLAG_COMPAT_V3` (default-on when whole word is zero), and
  `FLAG_BACKING_UNSAFE` (for `-u`).
- `backing_file_len > 0` ⇒ guest reads backing path from
  `backing_file[..len]`. The host writes the **user-typed**
  path bytes here (so the resulting metadata is portable), not
  the host-resolved absolute path.

## Public CLI surface

```text
instar create [OPTIONS] FILENAME [SIZE]

Arguments:
  FILENAME                  Path to the new image to create
  [SIZE]                    Virtual disk size (e.g. "1G", "512M").
                            Required unless -b BACKING is given.

Options:
  -f, --format <FMT>        Target format [default: raw]
                            [possible values: raw, qcow2, vmdk, vpc, vhdx]
  -b, --backing <PATH>      Backing file path (qcow2 / vmdk only in
                            phase 3; vhd/vhdx backing returns a clear
                            "not yet supported" error per the guest's
                            phase-2 limitation)
  -F, --backing-format <F>  Backing file format hint
  -u, --backing-unsafe      Don't verify backing file existence/format
  -q, --quiet               Suppress the "Created: ..." line on success

  --sector-size <N>         Host I/O sector size (power of 2, 512..=64K)
                            [default: 65536]
  --output <FMT>            Result rendering [default: human]
                            [possible values: human, json]

  -o, --option <KEY=VAL>    qemu-img-style options. Recognised in
                            phase 3 only as a passthrough placeholder
                            (the full parser ships in phase 4);
                            unknown keys return an error.

  Per-format option flags (also settable via -o in phase 4):
  --cluster-size <N>        qcow2 cluster size (default 65536)
  --refcount-bits <N>       qcow2 refcount entry width (default 16)
  --extended-l2             qcow2: emit extended-L2 entries
  --lazy-refcounts          qcow2: enable lazy refcounts
  --compat <V>              qcow2 compat [default: 1.1] (possible: 0.10, 1.1)
  --subformat <NAME>        vmdk: monolithicSparse|streamOptimized
                            vhd:  dynamic|fixed
  --grain-size <N>          vmdk grain size (default 65536)
  --block-size <N>          vhd/vhdx block size (default 2 MiB / 32 MiB)

  --preallocation <MODE>    [default: off]
                            Phase 3 accepts only "off" and "falloc"
                            (raw only). Other modes return a clear
                            "not yet supported" error pointing at
                            phase 6.
```

The `-o` and full preallocation handling are explicitly deferred.
Phase 4 expands `-o` into the same per-format option matrix
measure uses. Phase 6 wires preallocation through the guest's
`CreateConfig.flags` (phase 2 reserved the bits).

## Argument validation

Phase 3 host-side checks (defence in depth — the guest re-checks
critical fields):

- `FILENAME` required.
- Either `SIZE` or `-b BACKING` required (not both forbidden;
  explicit SIZE wins per the master plan).
- `--format` must be in {raw, qcow2, vmdk, vpc, vhdx}.
- `--sector-size` power of 2, 512..=64K.
- `--cluster-size` (qcow2) power of 2, 512..=2 MiB.
- `--refcount-bits` in {1, 2, 4, 8, 16, 32, 64}.
- `--grain-size` (vmdk) power of 2, 4 KiB..=64 KiB.
- `--block-size` (vhd: 512 KiB..=256 MiB; vhdx: 1 MiB..=256 MiB,
  power of 2).
- `--compat` in {"0.10", "1.1"}.
- `--subformat` valid for the chosen `--format`.
- `--preallocation` in {"off", "falloc"}; everything else errors.
- `-b BACKING` without `-F` or `-u` rejected (matches modern
  qemu-img); use `-u` to suppress.
- Output FILENAME's directory must be writable.

## Raw short-circuit path

For `-f raw`:

1. Resolve SIZE (must be provided — raw doesn't support `-b`).
2. Reject `-b` with a clear "raw doesn't support backing" error.
3. `open(FILENAME, O_CREAT|O_TRUNC|O_RDWR, 0644)`.
4. `ftruncate(fd, virtual_size)`.
5. If `--preallocation=falloc`: `posix_fallocate(fd, 0, size)`.
   (Other preallocation modes return the phase-6-deferred error.)
6. Sync metadata and close.
7. Render the result line (unless `-q`).

No guest launch. No KVM. No virtio. The whole raw path is ~30
lines of straightforward host I/O.

## Non-raw `run_create` flow

For qcow2 / vmdk / vhd / vhdx:

1. **Resolve virtual size.** If user gave SIZE: use it as-is. If
   user gave only `-b BACKING`: leave `CreateConfig.virtual_size`
   = 0 so the guest infers from the backing header.
2. **Open and attach output device.** `BackingStore::open(
   output_path, false, Some(capacity_hint), true)` then
   `VirtioBlockDevice::new(...)` + `device_set.add_device(...,
   false)`. The capacity hint can be set to a generous upper
   bound (e.g. `virtual_size + 64 MiB`) so the host doesn't
   pre-allocate the whole file; the file is sparse.
3. **Optionally open and attach backing.** If `-b` was given:
   `BackingStore::open(backing_path, true, None, false)` +
   add_device as input device 0.
4. **Populate `CreateConfig`.** Translate every flag/option into
   the corresponding struct field. Write to
   `OPERATION_CONFIG_ADDR`.
5. **Launch the guest.** Same KVM / vCPU / event-loop setup
   `run_measure` uses. Run until `send_complete`.
6. **Receive `CreateResultMessage`.** During the event loop,
   accumulate it just like measure does for
   `MeasureResultMessage` (the host already pattern-matches on
   `Payload::CreateResult` per step 2b).
7. **Render** human / json / quiet (see "Result rendering").
8. **Handle errors.** Non-zero `CreateResult.error` ⇒ remove the
   partially-written output file and surface a clear error to
   the user.

## Backing-file handling

Phase 3 implements the minimum needed for "default size from
backing":

1. The host accepts `-b BACKING [-F FMT] [-u]`.
2. The path the user typed is written verbatim into
   `CreateConfig.backing_file[..len]` (so the resulting image
   embeds a portable path).
3. The path is **resolved relative to the new image's directory**
   for opening (matches qemu-img). e.g. `instar create
   /tmp/new.qcow2 -b ../parent.qcow2` opens `/parent.qcow2`.
4. The backing file is attached as input device 0 — the guest's
   `read_backing_virtual_size` (phase 2e) reads its header.
5. Without `-u`, the host verifies the backing file exists and
   is readable. With `-u`, skip the existence check (matches
   qemu-img's `--backing-unsafe`).

Deferred to phase 5:
- vhdx-as-backing (the guest currently returns
  `ERROR_BACKING_PARSE_FAILED`; phase 3 maps this to a clear
  user-facing message).
- Backing-chain composition (more than one backing layer).
- Computing the real parent CID for vmdk backing (phase 1's
  builder uses a fixed sentinel; phase 5 will plumb the actual
  CID through).
- Backing-file-as-target (using qcow2 backing-format extension
  with raw backing, mismatched-format scenarios).

## Result rendering

### Human (default)

```text
Created: /path/to/foo.qcow2 (format=qcow2, virtual_size=1073741824, cluster_size=65536)
```

The line lists: filename, format, virtual_size (decimal bytes),
and the resolved unit size (cluster/grain/block) when non-zero.
Suppressed under `-q`.

### JSON

```json
{
    "filename": "/path/to/foo.qcow2",
    "format": "qcow2",
    "virtual_size": 1073741824,
    "metadata_bytes_written": 262144,
    "file_size_after": 262144,
    "resolved_unit_size": 65536
}
```

4-space indent, keys in the order shown. Mirrors measure's
`--output=json` formatting style. No `-q` interaction — JSON is
always emitted in JSON mode, on stdout.

### Errors

A non-zero `CreateResult.error` maps to one of:

| Code | User message |
|------|--------------|
| `ERROR_INVALID_OPTION` | `create: invalid option for target format` |
| `ERROR_INVALID_SIZE` | `create: virtual size out of range for format` |
| `ERROR_SCRATCH_TOO_SMALL` | `create: option combination exceeds guest scratch (try a larger cluster size)` |
| `ERROR_BACKING_READ_FAILED` | `create: failed to read backing file header` |
| `ERROR_BACKING_PARSE_FAILED` | `create: backing file format not supported (vhdx as backing is deferred — see PLAN-create.md phase 5)` |
| `ERROR_BACKING_TOO_LONG` | `create: backing file path too long (max 1024 bytes)` |
| `ERROR_WRITE_FAILED` | `create: write to output device failed` |
| `ERROR_UNSUPPORTED_FORMAT` | `create: target format not supported` |

In every error case the host removes the (likely partial) output
file before exiting with a non-zero status.

## Open questions

These should be answered during execution; escalate to the
management session rather than guessing.

1. **Sector size default.** Measure defaults `--sector-size` to
   65536. For create the *output* device's sector size matters
   for the metadata-write alignment; phase 1's emitters produce
   writes that are 512-aligned regardless. Recommend: same
   default as measure (65536) for consistency, with the same
   validation rules.

2. **Capacity hint for `BackingStore::open` on the output.**
   `BackingStore` needs to know roughly how big the file will be
   so it can allocate the right MMIO range. For qcow2 / vmdk /
   vhd dynamic / vhdx, the actual file is far smaller than
   `virtual_size` (just the metadata footprint). For vhd fixed
   and raw the file is `virtual_size + 512` or `virtual_size`.
   Recommendation: pass `virtual_size + 64 MiB` as a generous
   upper bound and let the underlying file stay sparse —
   matches what convert does.

3. **Phase 3's `-o` placeholder.** The master plan defers full
   `-o` parsing to phase 4. Two options for phase 3:
   (a) Accept `-o` as a free-form Vec<String> and return an
   "use individual flags in phase 3" error if any are passed;
   (b) Don't even expose `-o` until phase 4.
   Recommendation: (a) — exposing the flag now lets the help
   text be stable, and the placeholder error is a clean way to
   route users to the individual flags for now.

4. **What happens if the output file already exists?** qemu-img
   silently overwrites (`O_TRUNC`). instar matches that.
   Document the silent-overwrite in `docs/quirks.md` once
   phase 11 ships the docs.

5. **Argument parser library.** Existing subcommands use `clap`
   with `#[derive(Parser)]`. Reuse the same pattern.

6. **Default virtual size when `-b` is given without SIZE.** The
   host leaves `CreateConfig.virtual_size = 0`; the guest reads
   it from the backing's header. If the backing parse fails the
   user sees `ERROR_BACKING_PARSE_FAILED`. **Issue**: the host
   doesn't know the virtual size at output-attach time, so the
   capacity hint can't be derived from it. Workaround: use a
   conservative default (16 MiB minimum + a generous upper) and
   let the guest's metadata writes drive the actual file size
   via sparse writes. Alternative: have the host peek at the
   backing file's first sector with a tiny helper to derive
   `virtual_size` before attaching the output — but that
   re-introduces host-side parsing of untrusted format bytes,
   which violates the security model. Recommend: conservative
   default with sparse output. Document the implication
   (`BackingStore::open`'s capacity hint becomes an upper bound,
   not a target).

7. **Error path: should the host always delete a partially-
   created output?** Yes for non-raw (no in-place mutation
   semantics; the file only makes sense complete). For raw
   short-circuit: if `ftruncate` fails after `open(O_CREAT)`,
   yes delete. If `posix_fallocate` fails: probably keep the
   partial truncated file and let the user re-run. Recommend:
   always delete the partial output on any failure path. The
   cost is minor (one syscall) and the simplicity is worth it.

8. **`--object` clap surface for v1.** qemu-img uses `--object`
   for LUKS encryption keys. We defer encrypted-create per the
   master plan. Recommendation: don't include `--object` in
   phase 3's clap surface at all. Phase 11 / future revisits.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | medium | sonnet | none | Add `CreateArgs` struct and `Commands::Create(CreateArgs)` variant to `src/vmm/src/main.rs`. Mirror `MeasureArgs`'s shape (line ~2496 — `#[derive(clap::Args)]` with `long_about` doc, all flags from the "Public CLI surface" section above). Add a stub `fn run_create(args: CreateArgs, verbose: bool) -> Result<(), Box<dyn std::error::Error>>` that performs **only argument-level validation** (see "Argument validation" section) and returns `Err("phase 3b will implement the raw short-circuit; phase 3c the guest dispatch")` for any successful-validation path. Wire `Commands::Create(args) => run_create(args, verbose)` into the main dispatch. Verify `instar create --help` renders cleanly with `cargo run --release -- create --help` (or just check `make instar` builds). No tests in this step; phase 3f's smoke tests cover the integrated path. |
| 3b | medium | sonnet | none | Implement the raw short-circuit in `run_create`. For `-f raw`: validate SIZE was provided, reject `-b` with a clear error, open output with `O_CREAT|O_TRUNC|O_RDWR`, `ftruncate` to the virtual size, optionally `posix_fallocate` if `--preallocation=falloc`. Render the result line on success (or suppress under `-q`). On any failure, remove the partial output file before returning. Use the `nix` crate's `fcntl::posix_fallocate` if it's already in the workspace; otherwise call `libc::posix_fallocate` directly via the existing `unsafe extern "C"` pattern. No guest launch in this step. |
| 3c | high | opus | none | Implement the non-raw run_create path. Open the output with `BackingStore::open(path, false, Some(virtual_size + 64 MiB), true)`, attach as `add_device(..., false)`. Set up KVM/VM/vCPU/event-loop the same way `run_measure` does — including `kvm_stats`, guest memory creation, loading core + create binaries, and the standard run loop until `send_complete`. Populate a `CreateConfig` from `args` (every field except backing_file_*; that's step 3d). Skip backing attach for now — backing handling lands in step 3d. The result handling is minimal here: just check the error code and surface `Ok(())` or `Err("create failed: error code N")`. Pretty rendering lands in step 3e. Validate end-to-end by running `cargo run --release -- create -f qcow2 /tmp/foo.qcow2 1G` and then `cargo run --release -- info /tmp/foo.qcow2` and confirming the result. (This validation step requires `/dev/kvm` access — if the dev environment lacks it, document the manual smoke check in the commit message.) |
| 3d | high | opus | none | Add backing-file support to `run_create`. When `-b` was passed: resolve the user-typed path relative to the output file's parent directory; verify it exists (unless `-u`); `BackingStore::open(resolved, true, None, false)` and `add_device(..., true)` as input device 0. Set `CreateConfig.backing_file[..len]` to the **user-typed** bytes (not the resolved path) so the resulting metadata is portable; set `backing_file_len`; set `backing_format` from `-F` (mapped to the `ImageFormat` enum). Leave `CreateConfig.virtual_size = 0` when SIZE was not provided so the guest infers from the backing header. Smoke-test: `cargo run --release -- create -f qcow2 -b parent.qcow2 -F qcow2 child.qcow2` and verify `instar info child.qcow2` reports the right `virtual_size` and `backing_file`. |
| 3e | medium | sonnet | none | Implement result rendering. Move the "minimal error check" from step 3c into a proper renderer that handles `--output=human` (default — emit the qemu-img-style `Created: ...` line, suppress under `-q`), `--output=json` (4-space indent, key order matching the "JSON" section above), and the error-code-to-message table from the "Errors" section. On any non-zero `CreateResult.error`: remove the partially-written output file and return a `Box<dyn Error>` with the mapped message. Refactor `run_create` so the render logic is a separate helper that takes the result, the args, and a `&Path`. |
| 3f | medium | sonnet | none | Add `tests/test_create.py` with smoke tests for the happy path. Use the existing `InstarTestBase` pattern from `tests/test_measure.py`. Cover: (1) `instar create -f raw foo.raw 16M` → file is 16 MiB, sparse; (2) for each of qcow2 / vmdk / vhd / vhdx: `instar create -f FMT foo.FMT 16M` succeeds and `instar info foo.FMT` reports `virtual_size=16777216`; (3) `instar create -f qcow2 --cluster-size 4096 foo.qcow2 16M` succeeds and `instar info` reports `cluster_size=4096`; (4) backing-defaults-size: create a parent qcow2 with `instar create`, then `instar create -f qcow2 -b parent.qcow2 -F qcow2 child.qcow2` succeeds with `info` reporting matching `virtual_size`; (5) error: `instar create -f qcow2 foo.qcow2` without SIZE returns an error. Defer the full option matrix (every cluster_size, every refcount_bits, etc.) to phase 8. Use `tempfile.TemporaryDirectory()` for output paths so tests are hermetic. |
| 3g | low | sonnet | none | Update internal docs: (1) `CHANGELOG.md` — promote the phase 2 entry from "internal-only" to "available", adding a one-line summary of the CLI surface and a link to this phase plan; (2) `AGENTS.md` — remove the "built but unwired" qualifier on the create operation line; (3) `ARCHITECTURE.md` — extend the operations/create paragraph to describe the host CLI surface and the raw short-circuit. Defer `docs/create.md` and `docs/usage.md` to phase 11. |

## Out of scope for phase 3

Reminders so a sub-agent doesn't drift:

- No full `-o key=value` parser — only individual flags work. Phase 4 wires
  the qemu-img-style parser, reusing measure's helper.
- No preallocation modes beyond `off` (and `falloc` for raw).
  `metadata` / `full` return a clear "phase 6 will ship this" error.
- No backing-file edge cases beyond a single immediate parent
  (no chains, no vhdx-as-backing, no qcow2 backing-format mismatch
  handling). Phase 5 wires the polish.
- No multi-file VMDK subformats (`monolithicFlat`,
  `twoGbMaxExtent*`). The clap surface accepts the names and
  returns a clear "phase-5 follow-up" error. Phase 1's library
  also rejects these subformats — defence in depth.
- No `--object` clap surface — encryption is deferred. Document
  the absence in `docs/quirks.md` once phase 11 lands.
- No `docs/create.md` or `docs/usage.md` updates — those land in
  phase 11.
- No baseline-driven cross-version tests — that's phase 7's
  generator + phase 8's test harness.
- No fuzz harnesses — phase 9.
- No modifications to convert / measure / info / check / compare /
  copy. Adding `Commands::Create` and `run_create` is purely
  additive in the VMM.

## Success criteria

- `make instar` builds cleanly. `cargo run --release -- create
  --help` renders the full clap surface.
- `make lint` clean.
- `make test-rust` passes — no new rust tests in phase 3, but
  the `vmm` crate gains the new `run_create` and must still
  compile and lint clean.
- `make test-integration` includes `tests/test_create.py`'s
  smoke set (5–8 cases) and they all pass.
- `pre-commit run --all-files` clean.
- `instar create -f raw /tmp/foo.raw 16M` produces a 16 MiB sparse
  file (verify with `stat`).
- `instar create -f qcow2 /tmp/foo.qcow2 16M` produces a valid
  qcow2 file (verify with `instar info /tmp/foo.qcow2` and
  `qemu-img info /tmp/foo.qcow2` both reporting
  `virtual_size=16M`).
- `instar create -f qcow2 -b parent.qcow2 -F qcow2 child.qcow2`
  produces a child image whose virtual size matches the parent
  (verify with `instar info`).
- `git diff --stat phase-3-base..HEAD -- src/operations/` is
  empty (convert / measure / info / check / compare / copy /
  create-op all unchanged in phase 3 — host-only work).

## Bugs fixed during this work

(To be filled in.)

## Back brief

Before executing each step of this phase, please back brief the
operator as to your understanding of the step and how the work
you intend to do aligns with the brief. In particular, flag if
the brief refers to file/line locations that don't match what
you find when you read them (the survey was a snapshot; the
codebase may have moved).
