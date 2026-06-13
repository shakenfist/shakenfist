# PLAN-snapshot phase 04: host CLI for list mode

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (the closest analog
`run_map` in `src/vmm/src/main.rs:9515`, the `MapArgs` clap
surface at `src/vmm/src/main.rs:2650`, the `MapRenderer`
streaming output writer at `src/vmm/src/main.rs:9955`, the
`Commands` enum dispatch at `src/vmm/src/main.rs:2510` and
`:3213`, the `vmm_config_input_only` helper and how
`SerialDecoder` framing decodes `GuestMessage` payloads, the
phase-1 wire ABI's `SnapshotConfig` / `SnapshotEntryMessage` /
`SnapshotResultMessage` shapes in `src/shared/src/lib.rs` and
`crates/guest-protocol/proto/guest.proto`, the phase-3
guest binary entry point at
`src/operations/snapshot/src/main.rs`, the existing
`format_size_human(bytes, qemu_compat=true)` helper at
`src/vmm/src/main.rs:943`, and the libc bindings already
available in the `libc = "0.2"` dependency), and ground your
answers in what the code actually does today. Do not speculate
about the codebase when you could read it instead. Where a
question touches on `qemu-img snapshot -l` output format,
research as needed — `block/qcow2-snapshot.c::dump_one_snapshot`
in qemu source is the authoritative reference, along with
`qemu-img.c::dump_snapshots`.

Phase plans for the parent master plan live alongside it in
`docs/plans/` and are named
`PLAN-snapshot-phase-NN-<descriptive>.md`. The master plan is
[PLAN-snapshot.md](/components/instar/plans/PLAN-snapshot/). This is phase 4 of
fourteen.

I prefer one commit per logical change, and at minimum one
commit per phase. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

Phases 1–3 landed the wire ABI, the qcow2 streaming parser,
and the guest binary `snapshot.bin` that implements
`MODE_LIST`. Phase 4 wires the host side: a new `Snapshot`
variant on the `Commands` enum, a `SnapshotArgs` clap surface
matching `qemu-img snapshot`'s flags, a `run_snapshot` entry
point that launches the guest and consumes the streamed
`SnapshotEntryMessage` records plus the terminating
`SnapshotResultMessage`, and a `SnapshotRenderer` that emits
`qemu-img snapshot -l` byte-exact human output plus an
instar-extension JSON form.

After phase 4 lands, the user can run:

```
$ instar snapshot -l image.qcow2
Snapshot list:
ID      TAG               VM SIZE                DATE     VM CLOCK          ICOUNT
1       snap1                 0 B 2026-06-05 12:34:56  00:00:00.000               
2       snap2                 0 B 2026-06-05 12:35:00  00:00:00.000               
```

End-to-end. The mutating modes (`-a`, `-c`, `-d`) are clap-
recognised but the host CLI rejects them with a clear "not yet
implemented; arrives in PLAN-snapshot phase 9" error rather
than launching the guest (the guest binary stubs from phase 3
would also return `ERROR_INVALID_CONFIG`, but rejecting host-
side gives a sharper message and saves the VM boot).

### What phase 4 builds on

- **Phase-1 ABI**: `SnapshotConfig` at `OPERATION_CONFIG_ADDR`
  with magic `"SNAP" = 0x534E4150`, `MODE_LIST = 0`,
  `FLAG_QUIET = 1`, `FLAG_FORCE_SHARE = 2`, `FLAG_VERBOSE =
  1 << 31`. `SnapshotEntryMessage` carries the per-snapshot
  metadata on the wire. `SnapshotResultMessage` carries the
  mode echo, error, count, assigned id.
- **Phase-2 qcow2 surface**: streaming parser, no in-memory cap;
  irrelevant to the host but the guest binary uses it under
  the hood.
- **Phase-3 guest binary**: `src/target/release/snapshot.bin`,
  built and size-checked by `src/build.sh`. `MODE_LIST` is the
  only mode with real logic; the other three modes return
  `ERROR_INVALID_CONFIG` plus a verbose-print marker.
- **Existing host scaffolding**:
  - `Commands` enum at line 2510 with `Map(MapArgs)` as the
    most recent variant.
  - `main` dispatch at line 3213 with one `Commands::X(args)
    => run_x(args, verbose)` arm per subcommand.
  - `MapArgs` struct at line 2650 — closest shape to
    `SnapshotArgs` because both have `--image-opts` rejection,
    `--output` selector, and a single positional `FILENAME`.
  - `run_map` at line 9515 — closest flow analog: validates
    args, loads `core.bin` + `<op>.bin`, sets up KVM / guest
    memory, writes the per-op config struct at
    `OPERATION_CONFIG_ADDR`, opens the source device, runs
    the vCPU loop consuming streamed messages, and renders
    output with `MapRenderer`.
  - `MapRenderer` at line 9955 — streaming `begin` / `emit_*` /
    `finish` lifecycle with human and JSON modes. The output
    flow is byte-exact against `qemu-img map`. Phase 4's
    `SnapshotRenderer` follows the same shape but for the
    `qemu-img snapshot -l` format.
- **Existing helpers**:
  - `format_size_human(bytes, qemu_compat=true)` at
    `src/vmm/src/main.rs:943` — emits qemu-img-compatible
    `1.0 KiB` / `64 KiB` / `0 B` strings. Drop-in for the
    `VM SIZE` column.
  - `vmm_config_input_only(sector_size)` — builds the
    chain-config envelope for single-source operations. Reuse.
  - `libc::time_t`, `libc::localtime_r`, `libc::strftime` —
    the project's `libc = "0.2"` dep gives us all three; no
    new crate needed for date formatting.

### What phase 4 does not change

- The wire ABI (frozen since phase 1).
- The qcow2 crate (frozen since phase 2).
- The guest binary (frozen since phase 3).
- The fuzz harness (phase 12 extends fuzz coverage).
- Cross-version baselines (phase 10 generates these).
- Integration tests (phase 11 wires `tests/test_snapshot.py`).
- The mutating-mode flow (phase 9 owns `run_snapshot_mutate`
  or whatever the dispatch helper is called).

### Why ship list-only host CLI now

The master plan's open question 1 (which the master plan
resolved) settled this: list mode is high-value, low-risk,
and stable enough to ship to users independently of the
mutating modes. Holding back the host CLI until phases 6–8
finish would mean ~5 more phases of work before any user can
exercise `instar snapshot` — that's wrong for an
incrementally-shippable subcommand. Phase 4 ships list-only;
phase 9 adds the mutating-mode dispatch on top.

The mutating flags are still part of phase 4's clap surface
because:

1. Splitting the clap parser between phases 4 and 9 means a
   user who types `instar snapshot -c name image.qcow2`
   between phases 4 and 9 gets "unrecognized argument `-c`"
   instead of the friendlier "create not yet implemented"
   message.
2. The `qemu-img snapshot` parity surface (`-l`, `-a`, `-c`,
   `-d`, `-f`, `-q`, `-U`, `--image-opts`) is well-defined
   and small; landing it now is cheaper than landing it
   piecemeal.
3. Phase 9 only needs to flip the dispatch — the clap surface
   is unchanged.

### Why a new renderer instead of reusing `MapRenderer`

The two output shapes don't overlap: `map` emits per-extent
rows with offset/length/mapped/file columns; `snapshot -l`
emits per-snapshot rows with id/tag/size/date/clock/icount
columns. The only structural similarity is the streaming
`begin / emit / finish` lifecycle. Phase 4 adopts that
lifecycle but the renderer is its own struct.

## Mission and problem statement

After phase 4 lands:

1. `src/vmm/src/main.rs` has:
   - Host-side `SNAPSHOT_CONFIG_MAGIC` / `SNAPSHOT_*` /
     `SNAPSHOT_RESULT_ERROR_*` constants (the equivalent of
     `MAP_CONFIG_MAGIC` etc.) mirroring the phase-1 ABI. The
     values match `shared::SnapshotConfig::MAGIC`,
     `MODE_LIST`, `FLAG_QUIET`, `FLAG_FORCE_SHARE`,
     `FLAG_VERBOSE`, and the 13 error codes from phase 1.
   - A new `SnapshotArgs` struct mirroring `qemu-img snapshot`'s
     surface: positional `filename`, `-l`/`-a SNAPSHOT`/`-c
     NAME`/`-d SNAPSHOT` mode flags (mutually exclusive via a
     clap `ArgGroup` with `required=true`), `-f FORMAT`, `-q`,
     `-U/--force-share`, `--image-opts`,
     `--output={human,json}`, `--sector-size=N` (instar
     extension matching `MapArgs`).
   - A `Snapshot(SnapshotArgs)` variant on the `Commands`
     enum, placed after `Commands::Map(MapArgs)` at line
     2532–2533. The doc comment reads
     `/// List, apply, create, or delete qcow2 internal snapshots`.
   - A `Commands::Snapshot(args) => run_snapshot(args,
     verbose)` arm on the dispatch match at line 3224, placed
     after the `Map` arm.

2. `run_snapshot` is the entry point. It:
   - Rejects `--image-opts` immediately with the standard
     `"snapshot: --image-opts is not supported ..."` error,
     mirroring the map / measure pattern.
   - Validates `sector_size` (`>=512`, `<=MAX_SECTOR_SIZE`,
     power-of-two) — same shape as `run_map`.
   - Resolves the mode from `(args.list, args.apply,
     args.create, args.delete)`. Exactly one is set thanks to
     the clap ArgGroup. For mode != `MODE_LIST`, returns an
     error matching the format
     `"snapshot: mode <name> is not yet implemented in v1; arrives in PLAN-snapshot phase 9"`
     without launching the guest.
   - For `MODE_LIST`, dispatches into `run_snapshot_list`.

3. `run_snapshot_list` does the full guest launch:
   - Loads `core.bin` + `snapshot.bin` via `get_binary_path`
     and `load_guest_binary`, exactly like `run_map`.
   - Creates the KVM VM + guest memory region, exactly like
     `run_map`.
   - Writes the 320-byte `SnapshotConfig` at
     `OPERATION_CONFIG_ADDR` field-by-field. Layout per
     phase-1 ABI (`magic`, `mode`, `flags`, `sector_size`,
     `arg_len`, `_pad`, `arg[256]`, `_reserved[40]`). For
     list mode `arg_len = 0` and `arg` is left zeroed.
     `flags` carries `FLAG_VERBOSE` when `verbose` is set,
     `FLAG_QUIET` when `args.quiet` is set, `FLAG_FORCE_SHARE`
     when `args.force_share` is set.
   - Opens input device 0 read-only via `BackingStore::open`.
     No chain — snapshot list mode is single-image.
   - Queues a `vmm_config_input_only` chain-config envelope on
     the serial transmitter.
   - Runs the vCPU loop consuming `Payload::SnapshotEntry(e)`
     (one per snapshot) and capturing `Payload::SnapshotResult(r)`
     (the terminator). The loop handles `Hlt`, `IoOut(SERIAL_PORT)`,
     `IoIn(SERIAL_PORT)`, `IoOut(DEBUG_PORT)`, `MmioRead`,
     `MmioWrite`, `Shutdown`, `FailEntry`, exactly like
     `run_map`.
   - Renders streamed entries via a new `SnapshotRenderer`
     instance (described below).
   - On guest error (`result.error != 0`): writes a stderr
     message via `snapshot_error_message` (parallel to
     `map_error_message`) and exits non-zero. The partial
     output is left in place (matches qemu-img behaviour and
     matches map's BrokenPipe / error policy).
   - On guest success (`result.error == 0`): calls
     `renderer.finish()`, flushes the writer, exits zero.

4. `SnapshotRenderer` is a streaming output writer with a
   `begin / emit / finish` lifecycle parallel to
   `MapRenderer`:
   - `new(writer, output_format)` — output_format is one of
     `"human"` or `"json"`.
   - `begin() -> std::io::Result<()>` — for human mode, emits
     nothing yet (qemu-img's `Snapshot list:` prefix is held
     until the first entry arrives so empty-table case
     produces no output, matching qemu-img exactly); for JSON
     mode, writes the opening `[`.
   - `emit_snapshot(e: &SnapshotEntryMessage) ->
     std::io::Result<()>` — for human mode, if this is the
     first entry, lazily writes the
     `Snapshot list:\n` prefix and the header row
     (`ID      TAG              VM SIZE                DATE     VM CLOCK          ICOUNT\n`).
     Then writes the per-snapshot row formatted per the
     `qemu-img snapshot -l` rules described below. For JSON
     mode, writes the comma-separated JSON object.
   - `finish() -> std::io::Result<()>` — for human mode, no-op
     (the last row's `writeln!` already produced its newline);
     for JSON mode, writes `]\n`.
   - The renderer tracks `first_entry_emitted: bool` so
     `emit_snapshot` knows whether to write the header. JSON
     mode tracks `first_entry_json` for the inter-object
     `,\n` separator (parallel to `MapRenderer`).

5. `qemu-img snapshot -l` byte-exact human format:
   - **Correction (phase 4 implementation):** the format below
     describes the qemu v3–v5 layout (`VM SIZE` / `VM CLOCK` with
     spaces; widths 7/16/7/20/13/15; conditional `sn->date_sec ?
     " " : ""` separator; 2-digit hours in the clock). The
     installed qemu-img is **10.0.8**, whose
     `dump_one_snapshot` emits `VM_SIZE` / `VM_CLOCK` (underscores)
     with widths 7/16/**8**/**19**/15/**10**, a uniform single-
     space separator (no `sn->date_sec` quirk), 4-digit hours in
     the clock (`0000:00:00.000`), and `"--"` for absent
     `icount`. The phase 4 implementation matches v10. The text
     below is left as the historical record of the wrong-and-
     corrected exchange. The matching `_reserved` width in
     `SnapshotConfig` is also corrected from `[u8; 40]` (320
     bytes total) to `[u8; 32]` (312 bytes total), per
     `src/shared/src/lib.rs`.
   - Header: `qemu_printf("%-7s %-16s %7s %20s %13s %15s",
     "ID", "TAG", "VM SIZE", "DATE", "VM CLOCK", "ICOUNT")`
     plus `\n`. Note column titles are `VM SIZE`, `VM CLOCK`
     (with spaces, not underscores) and `ICOUNT`.
   - Data row: `qemu_printf("%-7s %-16s %7s%s%20s %13s %15s",
     id, name, vm_size_str, sep, date_str, clock_str,
     icount_str)` plus `\n`, where:
     - `id` from `entry.id`. qemu format-truncates if it
       exceeds 7 chars (the `-7s` width is a minimum; longer
       strings shift later columns right). Matches qemu.
     - `name` from `entry.name` (same width semantics).
     - `vm_size_str = format_size_human(entry.vm_state_size,
       true)` — reuses the existing helper.
     - `sep = " " if entry.date_sec_hi != 0 || entry.date_sec_lo
       != 0 else ""`. This matches qemu's `sn->date_sec ? " "
       : ""` separator. (When `date_sec` is exactly 0 — which
       only happens for hand-crafted or pathological images —
       qemu emits one fewer space before the DATE column. We
       mirror exactly.)
     - `date_str` formatted via `libc::localtime_r` +
       `libc::strftime("%Y-%m-%d %H:%M:%S")`. Empty if
       `date_sec` is 0.
     - `clock_str = format!("{:02}:{:02}:{:02}.{:03}", hours,
       minutes, seconds, milliseconds)` where the four values
       come from `entry.vm_clock_nsec`.
     - `icount_str = if entry.icount == u64::MAX {
       String::new() } else { entry.icount.to_string() }`.
       The `%15s` width still pads with spaces.

6. JSON output (instar extension):
   - Opening `[\n`.
   - One object per snapshot, comma-separated with `,\n`:
     ```json
     { "id": "1", "name": "snap1", "vm-state-size": 0,
       "date": { "seconds": 1717589696, "nanoseconds": 0 },
       "vm-clock": { "seconds": 0, "nanoseconds": 0 },
       "icount": 0 }
     ```
     Field order: `id` (string), `name` (string),
     `vm-state-size` (u64), `date` (object with `seconds`
     u64 and `nanoseconds` u32), `vm-clock` (object with
     `seconds` u64 and `nanoseconds` u64), `icount` (u64 or
     `null` if absent).
     The `seconds` field of `date` is `(date_sec_hi << 32) |
     date_sec_lo`; in practice `date_sec_hi == 0` until the
     year 2106.
     The `seconds` field of `vm-clock` is
     `vm_clock_nsec / 1_000_000_000`; `nanoseconds` is
     `vm_clock_nsec % 1_000_000_000`.
     `icount` is `null` when `entry.icount == u64::MAX`.
   - Closing `]\n`.
   - The key names mirror qemu's QMP `SnapshotInfo`
     (kebab-case `vm-state-size`, `vm-clock`).

7. `snapshot_error_message(error: u32) -> Option<&'static str>`
   maps `SnapshotResult::ERROR_*` codes to stderr-friendly
   messages. Returns `None` for `ERROR_OK`. Initial set:
   - `ERROR_UNSUPPORTED_FORMAT` → `"snapshot: source is not qcow2 (qemu-img refuses non-qcow2 sources too)"`
   - `ERROR_UNSUPPORTED_FEATURE` → `"snapshot: qcow2 image has an incompatible feature (compression / encryption / external data file / bitmaps); list mode should not return this, please report"`
   - `ERROR_IO` → `"snapshot: I/O failure reading the source"`
   - `ERROR_PARSE_FAILED` → `"snapshot: qcow2 header / snapshot-table parse failed"`
   - `ERROR_INVALID_CONFIG` → `"snapshot: invalid config (host-side bug; please report)"`
   - Catch-all → `"snapshot: unknown error code N"` (with the
     numeric code).
   The other error codes (`ERROR_NOT_FOUND`,
   `ERROR_DUPLICATE_NAME`, etc.) are populated by mutating
   modes; list mode shouldn't produce them but the catch-all
   handles future drift gracefully.

8. Unit tests for `SnapshotRenderer` mirror the existing
   `MapRenderer` test pattern (around line 12223 of
   `src/vmm/src/main.rs`). Coverage:
   - Human mode header row only emitted when at least one
     entry arrives.
   - Empty list: `begin` + `finish` with no `emit_snapshot`
     produces empty output in human mode, `[]\n` in JSON
     mode.
   - Single-snapshot human row format byte-exact against a
     known fixture.
   - Two-snapshot human output with comma-separated JSON.
   - `date_sec == 0` separator omission.
   - `icount == u64::MAX` produces empty `ICOUNT` column in
     human / `null` in JSON.
   - Long ID / long name shift later columns right (matches
     qemu's minimum-width semantics).
   - VM SIZE column uses `format_size_human(_, true)` —
     spot-check `0 B`, `1.0 KiB`, `64 KiB`.

9. `make instar` builds clean; `instar snapshot --help`
   renders; `instar snapshot -l <existing-qcow2-with-snap>`
   produces output matching `qemu-img snapshot -l` byte-for-byte
   for a known fixture (manual smoke test, step 4f).

10. `make lint` clean; `make test-rust` passes (the new
    `SnapshotRenderer` unit tests raise the workspace test
    count by ~9); `make check-binary-sizes` unchanged
    (host-only changes; no guest binary delta);
    `pre-commit run --all-files` clean.

11. `docs/quirks.md` gets a one-paragraph note: the date
    column is formatted in local time matching qemu-img; for
    deterministic CI / baselines pin `TZ=UTC`.

Nothing in phase 4 changes the wire format. `instar snapshot
-l` becomes the first user-visible end-to-end snapshot
operation.

## Open questions

### 1. Should phase 4 surface all four mode flags in clap?

Working answer: **yes** (already discussed in Situation). The
clap surface is the qemu-img parity surface; landing it now
saves a churnful "add `-a` / `-c` / `-d`" change in phase 9.
Phase 4 errors out on mutating modes at the host CLI level,
without launching the guest, with a friendly "not yet
implemented; arrives in PLAN-snapshot phase 9" message.

### 2. Should the rejected-mutating-mode error mention "phase 9" verbatim?

Working answer: **yes, mention the plan and phase**. The
user-facing error message is more helpful when it points at
the tracking artifact:

```
snapshot: -c (create) is not yet implemented in v1;
arrives in PLAN-snapshot phase 9 (see docs/plans/PLAN-snapshot.md)
```

A user who wants to know "when is this coming?" finds the
answer in the same place we're tracking it. Alternative
considered: vague "not yet supported" message. Rejected: the
project tracks deferred work in the plan files; the error
should too.

### 3. Should `run_snapshot` accept the `-f FORMAT` flag and validate against `qcow2`?

Working answer: **yes, accept it but only validate that it's
`qcow2` or absent**. `qemu-img snapshot -l` rejects non-qcow2
sources with `Format driver '<fmt>' does not support image
snapshots`. instar matches: if `-f` is supplied and the value
is not exactly `qcow2`, refuse with the equivalent error.
Absent `-f` lets the guest auto-detect; the guest refuses
non-qcow2 with `ERROR_UNSUPPORTED_FORMAT` (added by phase 3).

The guest-side check is the security boundary; the host-side
check is a friendlier early refusal so we don't boot a VM to
say "no". Both paths produce the same user-facing error
modulo wording.

### 4. Should phase 4 handle the `-U` / `--force-share` flag?

Working answer: **accept but no-op**. qemu-img's `-U`
disables the file-lock protocol; instar does not implement
file locking, so the flag is a host-side no-op accepted for
CLI compatibility. The flag is passed to the guest via the
`FLAG_FORCE_SHARE` bit so future host-side enforcement
(should we add file locking later) can act on it without
breaking CLI compatibility.

### 5. Should we handle the BrokenPipe case like `run_map` does?

Working answer: **yes, exactly the pattern**. A user piping
`instar snapshot -l image.qcow2 | head -1` should not error;
the renderer should detect `BrokenPipe`, stop emitting, and
let the vCPU loop drain to `Hlt`. Same pattern as
`run_map`'s `broken_pipe: bool` guard.

### 6. Should the human-mode date format pin TZ or use the system TZ?

Working answer: **use the system TZ via `libc::localtime_r`**,
matching qemu-img. For deterministic CI / cross-version
baselines, the baseline generator pins `TZ=UTC` (master plan
mentioned this). The phase-4 docs/quirks.md note records the
behaviour.

Alternative considered: hard-code UTC inside instar. Rejected:
that would diverge from qemu-img's output, which is the
parity goal.

### 7. Should the JSON `icount` field be `null` or omitted when absent?

Working answer: **`null`**. qemu's QMP uses `null` for absent
optional fields. Omitting the key entirely would also be
defensible but breaks consumers that always iterate the same
key set. `null` is the conservative choice.

### 8. Should phase 4 add a sentinel-fixture for the smoke test?

Working answer: **no**. The smoke test (step 4f) uses any
existing qcow2 with snapshots — easy to create on the fly
via `qemu-img create + qemu-img snapshot -c first`. Adding a
permanent fixture is phase 10's scope (baselines).

### 9. Should the renderer accept a `&mut dyn Write` or be generic over `W: Write`?

Working answer: **generic over `W: Write`**, mirroring
`MapRenderer`. Lets the unit tests pass a `Vec<u8>` directly
and avoids the dyn-dispatch overhead in the streaming path.

### 10. Should phase 4 emit "Snapshot list:" prefix even when the list is empty?

Working answer: **no**. qemu-img produces zero output when
`nb_snapshots == 0` (see `qemu-img.c::dump_snapshots`: it
returns early before printing the prefix). instar matches.
The renderer's `begin()` deliberately does not emit the
prefix; only `emit_snapshot()` does, on first call. JSON
mode `begin()` writes `[` and an empty list closes with `]\n`
producing `[]\n` — consistent with the existing
`MapRenderer`'s empty-list behaviour for JSON.

### 11. Should phase 4 add an integration test under `tests/`?

Working answer: **no** (deferred to phase 11). The integration
tests for snapshot live in `tests/test_snapshot.py` (added in
phase 11) and require a fixture corpus. Phase 4's manual
smoke test in step 4f covers the end-to-end path.

### 12. Should the date column use chrono / time crate instead of libc?

Working answer: **libc**. The VMM has 15 deps already and no
date/time crate; adding chrono or time-rs for one strftime
call is overkill. `libc::localtime_r` + `libc::strftime` is
~30 lines of unsafe and matches the project's minimal-dep
posture. The unsafe block is small, well-defined, and tested.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | sonnet | worktree | Add the host-side constants and the `SnapshotArgs` clap surface to `src/vmm/src/main.rs`. (i) Constants: after the `MAP_RESULT_ERROR_IO` block (around line 161), add a `// SnapshotConfig constants (must match shared::SnapshotConfig)` block defining `SNAPSHOT_CONFIG_MAGIC: u32 = 0x534E4150` ("SNAP"), `SNAPSHOT_CONFIG_MODE_LIST = 0`, `SNAPSHOT_CONFIG_MODE_APPLY = 1`, `SNAPSHOT_CONFIG_MODE_CREATE = 2`, `SNAPSHOT_CONFIG_MODE_DELETE = 3`, `SNAPSHOT_CONFIG_FLAG_QUIET = 1 << 0`, `SNAPSHOT_CONFIG_FLAG_FORCE_SHARE = 1 << 1`, `SNAPSHOT_CONFIG_FLAG_VERBOSE = 1 << 31`. Followed by a `// SnapshotResult constants` block with the 13 `SNAPSHOT_RESULT_ERROR_*` codes from phase 1 (0..=12, names per `src/shared/src/lib.rs`). (ii) Insert `SnapshotArgs` after `MapArgs` (line 2685): mirror the master plan's "Host CLI dispatch" block (around line 459-501 of `docs/plans/PLAN-snapshot.md`). Specifically: positional `filename: String`; `#[arg(short = 'l', long, group = "mode")] list: bool`; `#[arg(short = 'a', long, group = "mode", value_name = "SNAPSHOT")] apply: Option<String>`; `#[arg(short = 'c', long, group = "mode", value_name = "NAME")] create: Option<String>`; `#[arg(short = 'd', long, group = "mode", value_name = "SNAPSHOT")] delete: Option<String>`; `#[arg(short = 'f', long)] format: Option<String>`; `#[arg(short = 'q', long)] quiet: bool`; `#[arg(short = 'U', long = "force-share")] force_share: bool`; `#[arg(long = "image-opts")] image_opts: bool`; `#[arg(long, default_value = "human", value_parser = ["human", "json"])] output: String`; `#[arg(long, default_value = "65536")] sector_size: u32`. Add `#[group(id = "mode", required = true, multiple = false)]` at the struct level via clap's group attribute. (iii) Add the `Snapshot(SnapshotArgs)` variant to the `Commands` enum at line 2532 (after `Map(MapArgs)`), with doc `/// List, apply, create, or delete qcow2 internal snapshots`. (iv) Add `Commands::Snapshot(args) => run_snapshot(args, verbose),` to the dispatch match at line 3224 (after the `Map` arm). (v) Add a stub `fn run_snapshot(args: SnapshotArgs, verbose: bool) -> Result<(), Box<dyn std::error::Error>> { Err("not implemented yet".into()) }` somewhere reasonable (after `run_map`). Run `cargo build --workspace` until clean — clap will fail without the group attribute, so iterate. |
| 4b | medium | sonnet | worktree | Flesh out `run_snapshot` in `src/vmm/src/main.rs`. (i) Reject `--image-opts` immediately with `"snapshot: --image-opts is not supported (instar accepts FILENAME directly; see docs/quirks.md)"`. (ii) Validate `sector_size` per the `run_map` shape (`512..=MAX_SECTOR_SIZE`, power-of-two). (iii) Resolve mode: if `args.list`, `MODE_LIST`; else if `args.apply.is_some()`, `MODE_APPLY`; else if `args.create.is_some()`, `MODE_CREATE`; else if `args.delete.is_some()`, `MODE_DELETE`; else `unreachable!` (clap's `required=true` group enforces exactly one). (iv) For modes other than `MODE_LIST`, return `Err(format!("snapshot: -{} ({}) is not yet implemented in v1; arrives in PLAN-snapshot phase 9 (see docs/plans/PLAN-snapshot.md)", short, long).into())` with `short`/`long` derived from the mode (e.g. `'a' / "apply"`). (v) If `args.format` is `Some(ref f)` and `f != "qcow2"`, return `Err(format!("snapshot: format driver '{}' does not support image snapshots (qcow2 only)", f).into())`. (vi) For `MODE_LIST`, dispatch into a new `fn run_snapshot_list(args: &SnapshotArgs, verbose: bool) -> Result<(), Box<dyn std::error::Error>>`. The skeleton of `run_snapshot_list` should mirror `run_map` lines 9544–9669: read input metadata, load `core.bin` + `snapshot.bin`, set up KVM / VM / guest memory, write the `SnapshotConfig` (next step). Stop before the vCPU loop; step 4c adds that. Run `cargo build --workspace` until clean. |
| 4c | high | opus | worktree | Add the vCPU loop and message consumption to `run_snapshot_list` in `src/vmm/src/main.rs`. (i) Write the 320-byte `SnapshotConfig` at `OPERATION_CONFIG_ADDR` field-by-field (use `guest_mem.write_obj` for each field, mirroring `run_map`'s per-field writes at lines 9633–9638). Offsets per phase-1 ABI: 0=magic u32, 4=mode u32, 8=flags u32, 12=sector_size u32, 16=arg_len u32, 20=_pad u32, 24=arg [u8; 256], 280=_reserved [u8; 40]. `arg_len = 0` and `arg` left zeroed (page-zeroed). `flags = (if verbose { FLAG_VERBOSE } else { 0 }) \| (if args.quiet { FLAG_QUIET } else { 0 }) \| (if args.force_share { FLAG_FORCE_SHARE } else { 0 })`. (ii) Open device 0 read-only via `BackingStore::open(input_path, true, None, false)?`, wrap in a `VirtioBlockDevice::new`, add to `DeviceSet`. (iii) Set up the serial decoder, transmitter, debug buffer, vCPU registers, and the chain-config queue exactly like `run_map`. (iv) Set up the stdout `BufWriter` and a `SnapshotRenderer` instance (step 4d adds the type; for now the renderer can be a stub `struct SnapshotRenderer<'a, W>`). Call `renderer.begin()`. (v) Run the vCPU loop: on `Payload::SnapshotEntry(e)`, call `renderer.emit_snapshot(&e)`, handle BrokenPipe with the same pattern as `run_map`. On `Payload::SnapshotResult(r)`, capture into `snapshot_result: Option<...>`. Handle Hlt, IoOut/IoIn (SERIAL_PORT + DEBUG_PORT), MmioRead/MmioWrite, Shutdown, FailEntry, unknown exits. (vi) After the loop, if `vm_error` is `Some`, return it. If `broken_pipe`, return `Ok(())`. Otherwise extract `snapshot_result` (error if `None`), check `result.error` via `snapshot_error_message(result.error)`, write stderr message and exit non-zero if non-OK. On OK, call `renderer.finish()`, drop the writer to flush, return Ok. Use opus: the vCPU loop holds the serial framing, the device set, the message-pattern matches, and the renderer lifecycle in one method; getting the BrokenPipe handoff and the result-vs-error rendering correct requires holding the full flow in context. Run `make instar` until clean; the renderer can still be a stub at this point. |
| 4d | high | opus | worktree | Implement `SnapshotRenderer` in `src/vmm/src/main.rs`, placed after the `MapRenderer` impl (around line 10065). Specifically: (i) `enum SnapshotOutputFormat { Human, Json }`. (ii) `struct SnapshotRenderer<'a, W: std::io::Write> { writer: &'a mut W, output_format: SnapshotOutputFormat, first_entry_emitted: bool, first_entry_json: bool, has_any_icount: bool, snapshots_emitted: u64 }`. (iii) `impl<'a, W: std::io::Write> SnapshotRenderer<'a, W>` with `new`, `begin`, `emit_snapshot`, `finish` per mission item 4 above. (iv) Human mode `emit_snapshot`: lazily emit `"Snapshot list:\n"` and the header row `"ID      TAG              VM SIZE                DATE     VM CLOCK          ICOUNT\n"` on the first call (set `first_entry_emitted = true`). Build per-row strings: `id_str` from `entry.id` (raw); `name_str` from `entry.name`; `vm_size_str = format_size_human(entry.vm_state_size, true)`; `date_sec = ((entry.date_sec_hi as u64) << 32) | (entry.date_sec_lo as u64)`; `date_str` via libc localtime_r/strftime per item 5 (or empty if date_sec == 0); `sep = if date_sec != 0 { " " } else { "" }`; `clock_str = format!("{:02}:{:02}:{:02}.{:03}", h, m, s, ms)` where h=vm_clock_nsec/3_600_000_000_000, m=(vm_clock_nsec/60_000_000_000)%60, s=(vm_clock_nsec/1_000_000_000)%60, ms=(vm_clock_nsec/1_000_000)%1000; `icount_str` empty if entry.icount == u64::MAX else entry.icount.to_string(). Write the row with `writeln!(self.writer, "{:<7} {:<16} {:>7}{}{:>20} {:>13} {:>15}", id, name, vm_size, sep, date, clock, icount)`. Important: Rust's `{:<7}` is *minimum*-width left-aligned matching C printf's `%-7s` semantics; long ids/names shift later columns right correctly. (v) JSON mode `emit_snapshot`: on first entry, no comma; otherwise write `",\n"`. Build the per-snapshot JSON object exactly per mission item 6. `icount` is `null` when `entry.icount == u64::MAX`. (vi) `finish`: human mode no-op; JSON mode writes `"]\n"`. (vii) The libc date-formatting helper is a small private fn `fn format_qemu_date_local(date_sec: u64) -> String` that wraps `localtime_r` + `strftime` in a single unsafe block returning an owned `String`. Use opus: the format specifiers, the printf-to-Rust width-semantics translation, and the unsafe libc bindings need careful first-time correctness; reviewers will diff this against qemu's `dump_one_snapshot`. Run `make instar` and any inline unit tests until clean. |
| 4e | medium | sonnet | worktree | Add `snapshot_error_message(error: u32) -> Option<&'static str>` to `src/vmm/src/main.rs`, placed after `map_error_message` (around line 9910). Map the 13 `SNAPSHOT_RESULT_ERROR_*` codes from step 4a to user-facing strings per mission item 7. Wire it into `run_snapshot_list`'s post-loop result check from step 4c — replace any placeholder `"snapshot: guest error"` text with the resolved message. Confirm `make instar` builds clean. |
| 4f | medium | sonnet | worktree | Add unit tests for `SnapshotRenderer` to the existing `#[cfg(test)] mod tests` block at the end of `src/vmm/src/main.rs`. Pattern matches the existing `MapRenderer` tests around line 12223. Coverage per mission item 8: (a) `human_empty_list_emits_no_output`: begin + finish with zero emit_snapshot calls produces `""`. (b) `json_empty_list_emits_brackets`: begin + finish with zero emit_snapshot calls produces `"[]\n"`. (c) `human_single_snapshot_byte_exact`: known SnapshotEntryMessage fixture → byte-exact expected string (include the `Snapshot list:` prefix, header row, and the data row). (d) `human_two_snapshots_byte_exact`: two entries → two data rows. (e) `json_two_snapshots_comma_separated`: two entries → `[\n{...},\n{...}\n]\n`. (f) `human_date_sec_zero_omits_separator`: entry with date_sec_lo=0, date_sec_hi=0 → row uses the no-separator format (matches qemu). (g) `human_icount_absent_emits_blanks`: entry with icount=u64::MAX → row ends in 15 spaces. (h) `json_icount_absent_emits_null`: entry with icount=u64::MAX → JSON `"icount": null`. (i) `human_long_id_shifts_later_columns_right`: entry with id_len > 7 → later columns shift but format is parseable. Test setup: build `SnapshotEntryMessage` instances directly (it's a generated protobuf type with public fields per `crates/guest-protocol/src/lib.rs::snapshot_entry_message`). Use `Vec<u8>` as the writer. Run `cargo test -p instar` until clean. |
| 4g | low | sonnet | worktree | (i) Add a one-paragraph note to `docs/quirks.md`: "`instar snapshot -l` formats the DATE column in local time, matching `qemu-img snapshot -l`. For deterministic output (CI, cross-version baselines), set `TZ=UTC` before invoking instar." Place it under an appropriate existing section heading (the file has a "Date / time" or "Format-specific quirks" section per its existing structure; pick the closest match). (ii) Manual smoke test: build instar (`make instar`), create a qcow2 fixture with snapshots (`qemu-img create -f qcow2 /tmp/snap-test.qcow2 1M && qemu-img snapshot -c first /tmp/snap-test.qcow2 && qemu-img snapshot -c second /tmp/snap-test.qcow2`), run `instar snapshot -l /tmp/snap-test.qcow2`, run `qemu-img snapshot -l /tmp/snap-test.qcow2` under `TZ=UTC`, diff the two outputs. The instar output should match qemu-img byte-for-byte modulo the documented date-format quirk (instar uses local time without TZ=UTC; qemu-img does the same). Document the smoke-test recipe in the commit message body for future reproduction. (iii) Run `make instar`, `make test-rust`, `make check-binary-sizes`, `make lint`, and `pre-commit run --all-files`. Stage and present a single commit covering all of steps 4a–4f plus this docs/quirks.md edit, with the commit-message convention from `~/.claude/CLAUDE.md` (50-char first line ending in `.`, 75-char body wrap, Prompt paragraph, Signed-off-by, Co-Authored-By line with model + context window + effort + any other active settings). The commit message should explain that this lands the host CLI for `instar snapshot -l` end-to-end (qcow2-only, qemu-img byte-exact human + JSON-extension output), wires the full `qemu-img snapshot` clap surface (mutating modes recognised but rejected with a clear "arrives in phase 9" message), and the manual smoke-test recipe. |

## Agent guidance

### Execution model

All implementation work for this phase is done by sub-agents,
never in the management session. The management session (this
conversation) is reserved for review and decision-making.
After each step the management session:

1. Reads the actual files that were supposed to change.
2. Confirms no unrelated files were modified.
3. Runs the lint / test commands that the step's brief names.
4. Either commits, asks for a retry with a sharper brief, or
   upgrades the model.

All steps in this phase use `isolation: "worktree"`. Phase 4
spans clap surface + dispatch + renderer + tests + docs;
intermediate states (e.g. after step 4c but before step 4d
when the renderer is still a stub) are not shippable.

### Model and effort notes

- Steps 4a, 4b, 4e, 4f, 4g are mechanical extensions of
  well-established patterns (the `MapArgs` clap surface,
  `run_map` flow, `map_error_message`, the existing
  `MapRenderer` test block, and the existing quirks doc
  style). Sonnet at medium/low effort.
- Steps 4c and 4d are the load-bearing reasoning steps. 4c
  holds the full vCPU loop + serial framing + device set +
  BrokenPipe handling in context; 4d holds the printf-to-Rust
  format translation against qemu's reference C code plus
  the libc date-formatting unsafe block. Use opus.

### Management session review checklist

After each step:

- [ ] Read the changed files — don't trust the agent's
      summary.
- [ ] No unrelated files modified.
- [ ] `cargo build --workspace` (every step touching code).
- [ ] `make instar` (steps 4c, 4d, 4g).
- [ ] `cargo test -p instar` (step 4f).
- [ ] `make check-binary-sizes` (step 4g); no guest binary
      changes, all sizes unchanged.
- [ ] `make lint` (step 4g).
- [ ] `pre-commit run --all-files` (step 4g).
- [ ] The `SnapshotArgs` clap surface has the
      `#[group(id = "mode", required = true, multiple = false)]`
      attribute so `-l`, `-a`, `-c`, `-d` are mutually exclusive
      and exactly one is required.
- [ ] The renderer's `begin()` is empty for human mode (the
      `Snapshot list:` prefix is lazy on first emit, matching
      qemu-img empty-table behaviour).
- [ ] The `date_sec == 0` separator omission is implemented.
- [ ] `icount == u64::MAX` renders as 15 blanks in human and
      `null` in JSON.
- [ ] Mutating modes return the friendly "phase 9" error
      *without* launching the guest.

### Pre-commit verification ritual (step 4g)

The single commit at the end of step 4g must build cleanly
through the entire stack:

1. `make instar` — full host VMM + core + all guest
   operation binaries (snapshot.bin unchanged from phase 3).
2. `make test-rust` — workspace unit tests, including the
   new `SnapshotRenderer` tests from step 4f.
3. `make check-binary-sizes` — `snapshot.bin` unchanged; no
   other binaries affected.
4. `make lint` / `pre-commit run --all-files`.
5. Manual smoke test per step 4g(ii): byte-exact match
   against `qemu-img snapshot -l` under `TZ=UTC`.

If any of these fail, fix the failure in the *same* commit
(we are not amending a published commit; this is the
original commit's pre-push verification). Do not split into
a follow-up.

## Administration and logistics

### Success criteria

Phase 4 is complete when:

* All seven steps above land in one commit on the `snapshot`
  branch.
* `instar snapshot -l <qcow2-with-snapshots>` produces output
  matching `qemu-img snapshot -l` byte-for-byte (under
  `TZ=UTC` for both).
* `instar snapshot --output=json -l <qcow2-with-snapshots>`
  produces well-formed JSON with the documented field shape.
* `instar snapshot -c name <qcow2>` (and `-a`, `-d`) prints
  the friendly "not yet implemented; arrives in PLAN-snapshot
  phase 9" message and exits non-zero.
* `instar snapshot -l <raw-image>` reports the qemu-equivalent
  "non-qcow2" refusal.
* `make instar`, `make test-rust`, `make check-binary-sizes`,
  `make lint`, `pre-commit run --all-files` all pass.
* `docs/quirks.md` documents the TZ behaviour.

### Future work created by this phase

- **Phase 9 mutating-mode dispatch.** The clap surface is
  ready; phase 9 swaps the rejection arm in `run_snapshot`
  for real `run_snapshot_apply` / `run_snapshot_create` /
  `run_snapshot_delete` dispatch helpers and adds the
  open-RW input device wiring.
- **Cross-version baselines (phase 10).** The renderer's
  output is the input for phase 10's `snapshot-list-{human,json}`
  baseline generation against the qemu-img matrix in
  `instar-testdata/qemu-img-binaries/x86_64/`.
- **Integration tests (phase 11).** `tests/test_snapshot.py`
  drives `run_snapshot` against the new fixtures from phase
  10.
- **`Snapshot list:` row in `instar info`.** Currently
  deferred (master plan future work); revisit if user
  workflows surface a need.

### Bugs fixed during this work

This section will list any bugs encountered during
development that we fix in passing. The phase-1 ABI and
phase-2/3 surfaces are well-tested at this point; the host
CLI is thin glue. Any surprising behaviour would surface
during the manual smoke test in step 4g.

### Documentation index maintenance

This is a phase plan, not a master plan. It is **not** added
to `docs/plans/order.yml` per the convention. The master plan
links to it from the Execution table at
`docs/plans/PLAN-snapshot.md:866-882`; the commit message
points the master-plan execution-table reader at this file
(step 4g optional).

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
