# PLAN-bitmap phase 05: the host CLI

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read the actual
host code and ground every claim in it — in particular the amend
host CLI in `src/vmm/src/main.rs` (`AmendArgs`, `run_amend`,
`probe_amend_target`, `run_amend_guest`, `map_amend_error`,
`render_amend_success`), the snapshot **input-RW device attach**
(`run_snapshot_mutating_guest`), the clap `Commands` enum + the
top-level parse, the Phase-2 ABI (`BitmapConfig`/`BitmapResult`,
the `BitmapRunResult` holder + the `format_message` arm), the
Phase-4 guest op contract, and `qcow2::parse_header_extensions`.
Do not speculate when you could read. Consult the qcow2 spec and
`qemu-img.c`'s `img_bitmap` for the exact CLI grammar/validation.
Flag any uncertainty explicitly.

Phase plans live in `docs/plans/` as
`PLAN-bitmap-phase-NN-<descriptive>.md`. The master plan is
[PLAN-bitmap.md](/components/instar/plans/PLAN-bitmap/); Phases 1–4 are complete. This is
the fifth of ten phases — and the one that **finally makes `instar
bitmap` runnable end-to-end**, exercising the Phase-4 guest op for
the first time. Template: `amend` (CLI shape) + `snapshot` (device
attach). One commit per logical change, minimum one per phase.

## Situation

This phase adds the host side: the `BitmapArgs` clap surface, the
argument parsing/validation matching `qemu-img bitmap`, the
host-side qcow2 probe, `run_bitmap` / `run_bitmap_guest` (attaching
the image **input read-write** and harvesting the result), and
error/output rendering. When it lands, `instar bitmap --add …`
actually mutates an image — so this phase includes the **first
functional smoke test** of the whole Phases 1–4 stack (create a
qcow2, run `instar bitmap`, cross-check with `qemu-img info` +
`qemu-img check`). The formal integration suite is Phase 7; this
phase's smoke test is the go/no-go for whether the guest op works
at all.

Grounding (verified against HEAD `bcc39ab`):

- **clap is 4.5, derive-based** (`src/vmm/Cargo.toml:25`). The
  `Commands` enum is at `main.rs:2571-2603` (add `Bitmap(BitmapArgs)`);
  dispatch is a single `match cli.command` at `main.rs:3529-3545`.
  `AmendArgs` (`main.rs:2720-2739`) is the CLI template;
  `SnapshotArgs` (`main.rs:2896-2953`) shows an `ArgGroup` and the
  `--image-opts`/`--output`/`sector_size` fields.
- **No codebase precedent preserves cross-flag command-line
  order.** `ArgAction::Append` collects repeats of *one* flag (the
  `-o` collectors) but cannot interleave *different* flags. `qemu-img
  bitmap` applies `--add`/`--remove`/`--clear`/`--enable`/`--disable`/
  `--merge` in **CLI order**, repeatable — this is the one genuinely
  new bit of CLI plumbing (Open question 1).
- **Host probe** — `probe_amend_target` (`main.rs:5185-5215`) opens
  the file read-only, reads 4096 bytes, `qcow2::QcowHeader::parse`,
  and fills `ProbedAmendTarget` (`:5165-5178`) with `cluster_size`,
  `current_version`, `current_refcount_bits`,
  `current_incompatible_features`, `current_compatible_features`,
  `virtual_size`. **`QcowHeader` does NOT expose `autoclear_features`
  or the bitmaps extension** — the host must additionally call
  `qcow2::parse_header_extensions(header, &parsed)`
  (`src/crates/qcow2/src/lib.rs:491`) → `HeaderExtensionResults`
  (`bitmaps_present`, `bitmaps_inconsistent`, `bitmap_nb_bitmaps`,
  `bitmap_directory_size`, `bitmap_directory_offset`), and read the
  raw autoclear word via `qcow2::be_u64(buf,
  qcow2::AUTOCLEAR_FEATURES_OFFSET)` (offset 88). **`parse_header_
  extensions` is not called anywhere in main.rs today** — net-new.
  **Read the full first cluster** (not amend's 4096 B) so the
  extension chain is visible.
- **Config write + launch** — `run_amend` (`main.rs:5223-5312`)
  probes, builds `flags`, loads `core.bin` + `amend.bin`
  (`get_binary_path` `:1796`), opens the target, calls
  `run_amend_guest` (`:5423-5749`) which writes `AmendConfig`
  field-by-field at `OPERATION_CONFIG_ADDR`
  (`guest_mem.write_obj(value, GuestAddress(OPERATION_CONFIG_ADDR +
  off))`, `:5482-5505`), sets up KVM, launches, and harvests via the
  **`Payload::AmendResult` decode arm** (`:5652-5664`) into
  `AmendRunResult`. **bitmap's `BitmapRunResult` holder
  (`main.rs:2709-2715`) and the `format_message` pretty-print arm
  (`:935-941`) already exist (Phase 2); the run-loop harvest arm does
  NOT — this phase adds it** in a new `run_bitmap_guest`.
- **Input-RW device attach (bitmap needs this, like snapshot)** —
  `run_snapshot_mutating_guest` (`main.rs:12245-12265`):
  `BackingStore::open_rw_existing(path, Some(capacity_hint))`;
  `VirtioBlockDevice::new(backing, capacity_hint, sector_size,
  false /*read-write*/, mmio, vq)`; `device_set.add_device(dev,
  true /*is_input*/)` at slot 0. **`capacity_hint =
  input_size*2 .max(1<<30)`** — generous so the guest can grow the
  file. **bitmap grows the file** (add allocates table clusters,
  merge allocates data clusters, directory relocation allocates), so
  use snapshot's generous hint, NOT amend's file-size-only hint
  (Open question 4).
- **Error/output** — `map_amend_error(code) -> &str`
  (`main.rs:5317-5365`, exhaustive over `AmendResult::ERROR_*`);
  non-OK ⇒ `Err(format!("amend: {}", …))` to stderr.
  `render_amend_success` (`:5372-5413`) branches on `args.quiet` /
  `args.output`. amend prints "Image amended." — **bitmap must be
  SILENT on success by default** (qemu parity): print nothing in the
  human branch, only emit for `--output json` (Open question 5).
- **`--object`/`--image-opts` refusal** — resize's runtime reject
  (`main.rs:4144-4149`): `if args.object.is_some() { return
  Err("--object is not yet supported …") }` etc. Reuse for bitmap,
  plus reject `-b`/`-F` (cross-file merge deferred, master OQ2).
- **Host fsync after success** — amend reopens RW + `sync_all()`
  after `ACTION_AMENDED` (`main.rs:5296-5302`); bitmap does the same
  after a successful mutation (the guest also fsyncs during the
  dance; this is the host belt-and-suspenders).

## Mission and problem statement

Add the host CLI so `instar bitmap` runs end-to-end:

1. **`BitmapArgs` clap surface** matching `qemu-img bitmap`:
   ```
   instar bitmap (--add | --remove | --clear | --enable | --disable
                  | --merge SOURCE)... [-g GRANULARITY]
                  [-f FMT] [--output {human,json}] FILENAME BITMAP
   ```
   Action flags are **repeatable and applied in CLI order** (Open
   question 1). `-g/--granularity` (a size, accepts suffixes),
   `-f/--format`, `--output`, positionals `FILENAME` + `BITMAP`.
   Declare `--object`/`--image-opts`/`-b`/`-F` only to **reject**
   them at runtime (deferred/unsupported).

2. **Argument validation matching qemu** (`img_bitmap`), before any
   work, with qemu-comparable messages:
   - no actions ⇒ "Need at least one of --add, --remove, --clear,
     --enable, --disable, or --merge".
   - `-g` without `--add` ⇒ "granularity only supported with --add".
   - `-b`/`-F` present ⇒ "cross-file merge (-b/-F) is not yet
     supported by instar" (deferred).
   - `--object`/`--image-opts` ⇒ "not yet supported".
   - missing FILENAME or BITMAP ⇒ clap handles (both required
     positionals).
   - `> MAX_BITMAP_ACTIONS` (8) actions ⇒ a clear error.
   - a merge invocation mixed with metadata actions ⇒ reject
     (matching the Phase-4 guest restriction) with a clear message.

3. **Build the ordered action list + operands**: an in-CLI-order
   `Vec<(ActionOpcode, Option<source_name>)>`; the target `BITMAP`
   name; the granularity in bytes (0 ⇒ default; only for `--add`);
   the `--merge` source name(s). Map to `BitmapConfig`:
   `actions[..num_actions]` (opcodes), `name`/`name_len`,
   `granularity`, `merge_source_lens`/`merge_source_pool`
   (concatenated), `num_merge_sources`.

4. **Host probe**: open the file, read the full first cluster,
   `QcowHeader::parse` + `parse_header_extensions` + read autoclear
   @88, and fill the `BitmapConfig` cross-check
   (`cluster_size`, `current_version`, `current_refcount_bits`,
   `virtual_size`, `current_autoclear_features`,
   `current_incompatible_features`, `nb_bitmaps`,
   `bitmap_directory_offset`, `bitmap_directory_size`). Refuse
   non-qcow2 host-side with a clear error (defence before launch;
   the guest also refuses).

5. **`run_bitmap_guest`**: load `core.bin` + `bitmap.bin`, attach
   the target **input-RW at slot 0** (snapshot idiom, generous
   capacity hint), write `BitmapConfig` (scalars + the `actions`,
   `name`, `merge_source_lens`, `merge_source_pool` byte arrays)
   field/array-by-array at `OPERATION_CONFIG_ADDR`, launch, and
   harvest `BitmapResult` via a new `Payload::BitmapResult` decode
   arm into `BitmapRunResult` (`action`, `actions_applied`,
   `resulting_nb_bitmaps`, `error`).

6. **Render + fsync**: on a non-OK guest error ⇒ `Err(format!(
   "bitmap: {}", map_bitmap_error(error)))` to stderr (non-zero
   exit). On success ⇒ **silent** by default; `--output json` emits
   a small envelope (`actions_applied`, `resulting_nb_bitmaps`).
   Host `sync_all()` after a successful mutation.

7. **Smoke test** (the first end-to-end run): create a qcow2, run
   `instar bitmap --add`/`--remove`/`--enable`/`--disable`/`--merge`,
   and verify with `qemu-img info --output=json` (bitmap
   set/flags/granularity) + `qemu-img check` (no leaks/corruption).
   Any failure here is a Phase-4 bug to fix before the phase closes.

**Out of scope:** rust round-trip + full integration/baseline/fuzz
(Phases 6–9); cross-file `-b` merge (deferred). `instar info`
bitmap listing remains deferred (Phase 1 step 1e).

## Open questions

### 1. Capturing ordered action flags (the key CLI decision)

qemu applies actions in CLI order; clap derive with per-flag fields
loses cross-flag order. **Recommended: `indices_of` via
`ArgMatches`**, keeping qemu-faithful `--add`/`--remove` spelling:
- Declare the six action flags in `BitmapArgs`: the four bare flags
  (`--add`, `--clear`, `--enable`, `--disable` — wait, add is bare;
  actions with no value) as `#[arg(long, action =
  clap::ArgAction::Count)]` (Count records every occurrence so
  `indices_of` sees repeats), `--remove` likewise, and `--merge` as
  `#[arg(long, action = ArgAction::Append, value_name = "SOURCE")]
  merge: Vec<String>`.
- Change the top-level parse from `Cli::parse()` to the
  behaviour-preserving `let matches = <Cli as
  clap::CommandFactory>::command().get_matches(); let cli =
  Cli::from_arg_matches(&matches).unwrap_or_else(|e| e.exit());`
  (identical for every existing subcommand — `parse()` is exactly
  this internally), then pass `matches.subcommand_matches("bitmap")`
  to `run_bitmap` alongside the typed `BitmapArgs`.
- In `run_bitmap`, reconstruct order: for each action flag collect
  its `indices_of(name)` positions; for `--merge` zip the indices
  with `get_many::<String>("merge")` values; sort all
  `(index, opcode, source?)` by index ⇒ the ordered list.

Fallback if `ArgMatches` plumbing proves too invasive: a
`trailing_var_arg` token stream hand-parsed like
`parse_amend_o_options` (`main.rs:2760`) — but that muddies the two
positionals and `--help`, so prefer `indices_of`. Confirm the
mechanism in 5a.

### 2. `-g GRANULARITY` parsing

`-g` is a size in bytes accepting suffixes (`64k`, `1M`) — reuse the
existing size parser instar uses for resize/create sizes (grep
`parse_size`/`humansize`). 0/absent ⇒ the guest uses
`default_granularity`. Validate it maps to a `granularity_bits` in
`[9,31]` host-side too (fail fast) — or let the guest return
`ERROR_GRANULARITY_RANGE` and just pass the bytes. Prefer host-side
fail-fast with a clear message, matching qemu.

### 3. `--output` surface (master OQ11) — silent-by-default

`instar bitmap` is silent on success (qemu parity). Keep
`--output {human,json}`: human ⇒ print nothing; json ⇒ a small
envelope. Decide whether to keep a `-q/--quiet` flag — since human
is already silent it is redundant; **recommend omitting `-q`**
(qemu-img bitmap has none either — confirmed in the master-plan
research). Errors always go to stderr regardless.

### 4. Capacity hint — generous (grow the file)

Use snapshot's `input_size*2 .max(1<<30)` hint (bitmap allocates
new clusters). Confirm the guest's writes past EOF land correctly
with this hint (the smoke test validates).

### 5. sector_size

The guest's RMW helpers use `config.sector_size`. amend hardcodes
512; snapshot exposes `--sector-size` (default 65536). **Recommend
512** for bitmap (simple, and bitmap metadata writes are
sub-cluster) unless the smoke test shows a reason otherwise; do not
add a `--sector-size` flag (qemu-img bitmap has none).

### 6. Host-side pre-validation vs guest gates

The guest already gates (v2, dirty/corrupt, refcount width,
autoclear-inconsistent, cross-check). The host should still
fail-fast on the obvious (non-qcow2; and optionally surface the
guest's structured error clearly). Avoid duplicating deep gates
host-side beyond the cross-check probe — let the guest be the
authority and `map_bitmap_error` render its code.

## Step-level guidance

Plan at **high** effort (5a's ordering plumbing and 5b's guest
launch/harvest are the load-bearing pieces; this phase is where the
whole stack first runs). Skew to opus.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | high | opus | none | The CLI surface + ordered-action parsing + validation. Add `Bitmap(BitmapArgs)` to `Commands` (`main.rs:2571`) and the dispatch arm (`:3529`). Define `BitmapArgs` (derive): positionals `filename` + `bitmap`; the six action flags per Open question 1 (Count for the bare ones, Append `Vec<String>` for `--merge`); `-g/--granularity` (Option<String>, size); `-f/--format`; `--output` (human/json, default human); and `object`/`image_opts`/`source_file`(-b)/`source_format`(-F) declared only to reject. Change the top-level parse to `get_matches()` + `from_arg_matches` (behaviour-preserving) and thread the bitmap subcommand `ArgMatches` to `run_bitmap`. In `run_bitmap`: reject `--object`/`--image-opts`/`-b`/`-F` (resize-style); reconstruct the ordered action list via `indices_of`; run the qemu-parity validation (no actions; `-g` without `--add`; >8 actions; merge-mixed-with-metadata); parse granularity → bytes (Open question 2); resolve the target name + merge source name(s). For this step, after building + validating, return a clear "not yet wired" error (5b wires the launch) OR call a stubbed `run_bitmap_guest`. GATE: `instar bitmap --help` works; the validation errors match qemu; builds + `make lint`. |
| 5b | high | opus | none | The probe + config population + guest launch + harvest. `probe_bitmap_target` (model `probe_amend_target` `:5185`): open RO, read the FULL first cluster, `QcowHeader::parse` + `parse_header_extensions` + `be_u64(buf, AUTOCLEAR_FEATURES_OFFSET)`; refuse non-qcow2; return the cross-check fields. `run_bitmap_guest` (model `run_amend_guest` `:5423` for VM/launch, but use snapshot's **input-RW attach** `:12245-12265`: `open_rw_existing`, `VirtioBlockDevice::new(.., false, ..)`, `add_device(.., true)` at slot 0, generous capacity hint): write `BitmapConfig` at `OPERATION_CONFIG_ADDR` — the scalars via `write_obj`, and the `actions[8]`/`name[1024]`/`merge_source_lens[8]`/`merge_source_pool[2048]` arrays by writing their bytes (a slice write helper or a loop of `write_obj`); launch; harvest via a new `Payload::BitmapResult` decode arm (model `:5652-5664`) into `BitmapRunResult`. GATE: builds, `make lint`; wired but rendering is 5c. |
| 5c | high | opus | none | Rendering + fsync + the first end-to-end smoke test. `map_bitmap_error(code) -> &str` (exhaustive over `BitmapResult::ERROR_*` 0..=19, model `map_amend_error` `:5317`). `render_bitmap_success`: silent human branch, `--output json` envelope. Wire `run_bitmap`: probe → run_bitmap_guest → on non-OK error `Err(format!("bitmap: {}", map_bitmap_error(...)))` → on success `sync_all()` + render. SMOKE TEST (the go/no-go — /dev/kvm + qemu-img are available): create a qcow2 (`qemu-img create -f qcow2`), then exercise `instar bitmap --add bm0`, `--add -g 128k bm1`, `--disable bm0`, `--enable bm0`, `--clear bm0`, `--merge` (same-image), `--remove bm0`, each cross-checked with `qemu-img info --output=json` (name/flags/granularity vs qemu-img's own equivalent) and `qemu-img check` (clean). Also test the refusals (v2 image, non-qcow2, duplicate add, missing bitmap). Compare instar-produced images against `qemu-img bitmap` equivalents where possible. FIX any Phase-4 bug the smoke test exposes (report it clearly; a guest-op fix may be needed). GATE: the smoke test passes; `make instar`/`make lint`/`make test-rust`/`make check-binary-sizes`/`pre-commit run --all-files` green. |

## Management session review checklist

- [ ] Intended files changed (`src/vmm/src/main.rs` + smoke-test
      scratch only), no others.
- [ ] Ordered action flags are captured in true CLI order; the
      top-level parse change is behaviour-preserving for all other
      subcommands.
- [ ] qemu-parity argument validation + messages; `--object`/
      `--image-opts`/`-b`/`-F` refused.
- [ ] Input-RW attach at slot 0 with a growth-capable capacity hint;
      `BitmapConfig` (incl. the byte arrays) written correctly.
- [ ] The `Payload::BitmapResult` harvest arm populates
      `BitmapRunResult`; `map_bitmap_error` is exhaustive; success is
      silent (json opt-in); host `sync_all()` on success.
- [ ] **The smoke test passes**: `instar bitmap` actions produce
      images `qemu-img info`/`check` accept and that match
      `qemu-img bitmap`; refusals behave; any Phase-4 bug found is
      fixed.
- [ ] `make instar`/`make lint`/`make test-rust`/
      `make check-binary-sizes`/`pre-commit run --all-files` green.
- [ ] Commit messages follow conventions.

## Success criteria

- `instar bitmap` parses the qemu-compatible surface (ordered
  actions, `-g`, `-f`, `--output`), validates like `qemu-img
  bitmap`, and refuses the unsupported flags.
- It probes the qcow2 header + bitmaps extension, launches the
  Phase-4 guest op with a correct `BitmapConfig` over an input-RW
  device, harvests the result, renders silently (json opt-in), and
  fsyncs.
- The smoke test demonstrates real add/remove/clear/enable/disable/
  merge producing `qemu-img check`-clean, `qemu-img info`-consistent
  images, and correct refusals — the first end-to-end proof of
  Phases 1–4.
- All gates green.

## Back brief

Before executing any step, the executing agent should back brief
the operator — in particular that this phase **first runs the whole
stack** (so the 5c smoke test is the real correctness gate and may
surface Phase-4 bugs to fix), that action flags must be captured in
**CLI order** (`indices_of`), that the image is attached **input-RW
with a growth-capable capacity hint** (bitmap allocates clusters),
and that success output is **silent by default** (qemu parity).
