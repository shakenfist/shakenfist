# PLAN-amend phase 04: host VMM subcommand

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (the clap `Commands`
enum and per-op `*Args` structs, the `-o` option parsers, the
host-side image probes, the `run_*` / `run_*_guest` split, the
KVM-launch + config-write + result-harvest mechanism, the
human/json renderers), and ground your answers in what the code
actually does today. Do not speculate when you could read the
code. Where a question touches the qcow2 format or `qemu-img
amend`'s CLI surface, research as needed. Flag uncertainty rather
than guessing.

Phase plans live in `docs/plans/` named
`PLAN-amend-phase-NN-<descriptive>.md`. The master plan is
[PLAN-amend.md](/components/instar/plans/PLAN-amend/); phases 1–3 (ABI, planner, guest
op) are landed. This is the fourth of nine and the first phase
that makes `instar amend` runnable end-to-end.

I prefer one commit per logical change, and at minimum one commit
per phase. Each commit should be self-contained: it should build,
pass tests, and have a clear commit message explaining what changed
and why.

## Situation

Phases 1–3 landed the ABI (`AmendConfig`/`AmendResult`,
`send_amend_result`, the proto), the pure planner
`src/crates/amend/`, and the guest binary `src/operations/amend/`
(`amend.bin`, built and wired). But `amend` is unreachable: the
clap `Commands` enum has no `Amend` variant, so `instar amend`
prints "unrecognized subcommand".

This phase adds the **host VMM subcommand**: the clap surface, the
`-o` parser, a host-side probe that populates `AmendConfig`'s
cross-check fields, the KVM launch (`run_amend_guest`), the result
harvest, and the human/json renderer. After it, `instar amend -o
compat=1.1 FILE` works.

**Design stance: the host is thin.** All the amend *logic*
(refusals, downgrade gates, no-op detection, the byte-exact
rewrite) already lives in the planner (phase 2) and runs in the
guest (phase 3). The host only: (1) parses `-o` and rejects
unsupported keys with a clear CLI error *before* spinning up a VM;
(2) probes the qcow2 header to fill `AmendConfig`'s cross-check
summary; (3) launches the guest; (4) maps `result.error` to a
message and renders. It does **not** duplicate the planner's
refusal logic — those surface as guest error codes.

Grounding (verified on the `amend` branch):

- **Clap surface.** `Commands` enum at `src/vmm/src/main.rs:2560`;
  `ResizeArgs` at `:2590` and `RebaseArgs` at `:2633` are the
  templates (`filename` positional, `-f/--format`, `-q/--quiet`,
  `--output {human,json}` with `default_value="human"`). The
  repeated `-o` option pattern (create/measure) is `#[arg(short =
  'o', long = "options", action = clap::ArgAction::Append,
  value_name = "KEY=VALUE,...")] option: Vec<String>`
  (`:3243`/`:3346`). `main()` dispatches `Commands::Resize(args) =>
  run_resize(args, verbose)` at `:3387`.
- **`-o` parsing.** `parse_o_options` (`:8827` qcow2 arm) shows the
  exact key/value grammar to mirror: `("qcow2","compat")` accepts
  `"0.10"`→v2 / `"1.1"`→v3 (else error "expected 0.10 or 1.1");
  `("qcow2","lazy_refcounts")` uses `parse_o_bool` (`:8761`,
  on/true/yes ↔ off/false/no). The same block *rejects*
  `cluster_size`/`refcount_bits`/`extended_l2`/`compression_type`/
  `preallocation`/`backing_file`/`backing_fmt`/`data_file`/
  `data_file_raw`/`encrypt.*` — amend rejects all of these too
  (they change structure or are owned by other subcommands).
- **`run_resize`** (`:3990`) is the handler template: reject the
  unsupported surface up front with clear errors, parse args, call
  the host probe (`probe_resize_target` `:4038`), then dispatch to
  the guest-launch function. **`probe_resize_target`** opens the
  file read-only, reads 4 KiB, `detect_format_from_header`, and for
  qcow2 calls `qcow2::QcowHeader::parse(&buf)` host-side — exactly
  the mechanism amend's probe needs (the VMM depends on the `qcow2`
  crate). `QcowHeader` exposes `version`, `refcount_bits`,
  `incompatible_features`, `compatible_features`, `cluster_size`,
  `virtual_size` (`crates/qcow2/src/lib.rs:292`).
- **`run_resize_guest`** (`:4685`) is the launch template: KVM/VM
  setup, `guest_mem.write_slice(core_code, GUEST_CODE_BASE)` +
  `write_slice(operation_code, OPERATION_LOAD_ADDR)`, the
  field-by-field `*Config` write at `OPERATION_CONFIG_ADDR`
  (`:4726`), the stub-input-at-slot-0 + output-O_RDWR-at-slot-1
  device set (`:4770`), and the vCPU loop harvesting
  `Payload::ResizeResult` off the serial channel (`:4882`) into a
  `ResizeRunResult`. The op binary is loaded from the filesystem
  via `get_binary_path("resize.bin")` + `load_guest_binary`
  (`:4568`) — there is **no `include_bytes!` table**; amend uses
  `get_binary_path("amend.bin")`.
- **Phase-1 leftovers to use.** `AmendRunResult` holder already
  exists (`:2680`: `target_format`, `action`, `resulting_version`,
  `resulting_lazy_refcounts`, `error`), and a `Payload::AmendResult`
  arm exists in `format_message` (`:924`) for *debug logging only*.
  Phase 4 adds the real **harvest arm** in `run_amend_guest`'s vCPU
  loop. The proto `AmendResultMessage` carries `target_format`
  as a **string**, `action`+`resulting_version` as **u32**, and
  `lazy_refcounts` as **bool** — harvest the numerics; do not
  `parse::<u32>()` the format string (the host already knows the
  format code it probed).
- **Render + fsync.** `render_resize_success` (`:4270`) shows the
  quiet/json/human split (`json_escape_string`,
  `image_format_name`). Resize fsyncs the output with
  `file.sync_all()` after the guest halts (`:4661`) — amend must do
  the same after a successful rewrite to honour phase 3's
  crash-safety contract ("the host fsyncs the output file after the
  guest halts"). A guest `result.error != ERROR_OK` becomes a
  non-zero process exit via `Err(...)` from `run_amend`.

## Mission and problem statement

After this phase, `instar amend` works:

```
instar amend [-f qcow2] -o compat=0.10|1.1[,lazy_refcounts=on|off] \
             [-q] [--output {human,json}] FILENAME
```

1. **Clap.** Add `Amend(AmendArgs)` to `Commands`; `AmendArgs` =
   `{ filename, -f/--format, -q/--quiet, --output (human|json),
   -o/--options Vec<String> }` (no `--object`/`--image-opts` — not
   in amend's v1 surface). `main()` routes `Commands::Amend(args)
   => run_amend(args, verbose)`.

2. **`-o` parser** (`parse_amend_o_options`): accepts only the
   qcow2 keys `compat` (`0.10`→v2 / `1.1`→v3, else error) and
   `lazy_refcounts` (on/off via `parse_o_bool`-style). Every other
   key → a clear `"amend: -o key '<k>' is not supported (amend
   changes compat and lazy_refcounts only)"`. Returns
   `{ compat_v3: Option<bool>, lazy_on: Option<bool> }`. If **no**
   supported option was given → error `"amend: no supported -o
   options given (expected compat= and/or lazy_refcounts=)"`. This
   is a pure function and gets **unit tests**.

3. **Probe + config.** `run_amend` rejects an empty/unsupported
   option set up front, probes the file (open read-only, read
   4 KiB, `detect_format_from_header`; non-qcow2 → clear error,
   amend is qcow2-only), parses the qcow2 header host-side, and
   builds `AmendConfig`:
   - `magic = AMND`, `target_format = IMAGE_FORMAT_QCOW2`,
     `sector_size = 512`.
   - `flags`: `FLAG_SET_COMPAT` (+`FLAG_COMPAT_V3` if 1.1) when
     `compat` was given; `FLAG_SET_LAZY` (+`FLAG_LAZY_ON` if on)
     when `lazy_refcounts` was given; `FLAG_QUIET` if `-q`.
   - cross-check fields from the parsed header: `cluster_size`,
     `current_version`, `current_refcount_bits`,
     `current_incompatible_features`, `current_compatible_features`,
     `virtual_size`.

4. **Launch** (`run_amend_guest`, modelled on `run_resize_guest`):
   load `core.bin` + `amend.bin`, write `AmendConfig` field-by-field
   at `OPERATION_CONFIG_ADDR`, attach a 1-sector stub input at slot
   0 and the target file (opened **O_RDWR**) as the output at slot
   1, run the vCPU loop, and harvest `Payload::AmendResult` into an
   `AmendRunResult` (numeric `action`/`resulting_version`/`error`,
   `lazy_refcounts` bool→u32; `target_format` = the host's probed
   code). Error if the guest returns no result.

5. **Report.** If `result.error != AmendResult::ERROR_OK`, return
   `Err(format!("amend: {}", map_amend_error(result.error)))`
   (non-zero exit). On success: if the action was `AMENDED`,
   re-open the file and `sync_all()` (the durability fsync); then
   `render_amend_success` (unless `-q`): human prints `"Image
   amended."`/`"No change."`; json prints `{ filename, format,
   action, compat, lazy_refcounts }`. A `NoOp` exits 0 without
   touching the file.

6. `make instar` builds; `make lint`/`make test-rust`/`pre-commit`
   clean; `instar amend` runs end-to-end (verified in review
   against qemu fixtures — see the review checklist).

Out of scope: integration tests + cross-version baselines (phases
6–7); `usage.md`/`CHANGELOG` docs (phase 9); the deferred `-o`
keys and non-qcow2 formats (master plan).

## Open questions

### 1. `AmendArgs` and the `-o` parser shape (confirm before 4a)

Working draft:

```rust
#[derive(Args, Debug)]
struct AmendArgs {
    /// Image file to amend (qcow2 only).
    filename: String,
    /// Force the image format detection (qcow2 only).
    #[arg(short = 'f', long)]
    format: Option<String>,
    /// Suppress the success line on stdout. Errors still go to stderr.
    #[arg(short = 'q', long)]
    quiet: bool,
    /// Output format.
    #[arg(long, default_value = "human", value_parser = ["human", "json"])]
    output: String,
    /// qemu-img-style options, comma-separated key=value
    /// (e.g. -o compat=1.1,lazy_refcounts=on). Only compat and
    /// lazy_refcounts are supported.
    #[arg(short = 'o', long = "options", action = clap::ArgAction::Append,
          value_name = "KEY=VALUE,...")]
    option: Vec<String>,
}

struct AmendOOptions { compat_v3: Option<bool>, lazy_on: Option<bool> }
fn parse_amend_o_options(raw: &[String]) -> Result<AmendOOptions, Box<dyn std::error::Error>>;
```

Confirm the field set and that `-f` accepts only `qcow2` (any
other forced format → error; amend is qcow2-only).

### 2. No-op rendering wording

When the guest returns `ACTION_NOOP` (requested state already
matches), human output is `"No change."` and json `action` is
`"noop"`. Confirm this wording (phase 6 reconciles against qemu's
observable behaviour; qemu-img amend on a no-op may still rewrite
the header, but `qemu-img info` is unchanged either way).

### 3. The durability fsync

Phase 3 resolved crash-safety as "direct write; the host fsyncs the
output file after the guest halts." So `run_amend` **must**
`sync_all()` after a successful `AMENDED` result, mirroring resize
(`:4661`). Confirm we do this by re-opening the file read+write and
`sync_all()` (resize's pattern) rather than relying on
`BackingStore` drop — the explicit fsync is the load-bearing half
of the OQ6 contract. (A `NoOp` wrote nothing, so no fsync.)

### 4. Should the host pre-validate, or defer to the guest?

Recommendation: **defer**. The planner/guest already refuse dirty
images, downgrade-blocking features, `refcount_bits != 16`, and
lazy-on-v2, returning specific error codes. `run_amend` maps those
via `map_amend_error` to messages. The host pre-rejects only what
it can cheaply and unambiguously determine *before* a VM launch:
unsupported `-o` keys (CLI grammar) and non-qcow2 format (the
probe). Duplicating the planner's gates host-side would risk drift.
Confirm.

### 5. End-to-end verification in this phase

Because phase 4 makes `instar amend` runnable, the management
review should **actually run it** against real qemu fixtures
(create v2/v3 images with `qemu-img`, amend with `instar`, verify
with `qemu-img info`/`check`/`compare`), not just build it. This
needs `/dev/kvm`. Confirm the review will smoke-test:
upgrade v2→1.1, downgrade v3→0.10, lazy on/off, a v2-with-backing
upgrade (backing reference preserved), and a no-op. This is the
payoff of the phase and de-risks phase 6.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | sonnet | none | Add the clap surface and the `-o` parser to `src/vmm/src/main.rs`. Add `Amend(AmendArgs)` to the `Commands` enum (`:2560`) with a doc comment; define `AmendArgs` per Open question 1 (after `RebaseArgs`/before the next struct, matching style). Add `Commands::Amend(args) => run_amend(args, verbose),` to the `main()` dispatch (`:3387` neighbourhood). Write `parse_amend_o_options(raw: &[String]) -> Result<AmendOOptions, Box<dyn std::error::Error>>`: split each entry on `,` then `=`, match `compat` (`"0.10"`→`compat_v3=Some(false)`, `"1.1"`→`Some(true)`, else error "expected 0.10 or 1.1") and `lazy_refcounts` (on/true/yes→true, off/false/no→false, else error), reject every other key with the not-supported message, and error if neither option was set. Add a temporary `fn run_amend(args: AmendArgs, verbose: bool) -> Result<(), Box<dyn std::error::Error>>` stub that calls `parse_amend_o_options` and returns `Err("amend: not yet implemented".into())` so the build links. Add inline `#[cfg(test)]` unit tests for `parse_amend_o_options`: compat 0.10/1.1, lazy on/off, both together, bad compat value, bad lazy value, an unsupported key (e.g. `cluster_size`, `refcount_bits`), and the empty/no-supported-option case. Validate with `pre-commit run --all-files` and `make test-rust` (do NOT run cargo directly — sandbox-denied). |
| 4b | high | opus | none | Implement the host probe + `run_amend` orchestration + renderer + error map in `src/vmm/src/main.rs`. `probe_amend_target(path, forced_format) -> Result<ProbedAmendTarget, _>` modelled on `probe_resize_target` (`:4038`): open read-only, read 4 KiB, `detect_format_from_header` (or honour `-f qcow2`; reject any other forced/detected format with "amend: only qcow2 images can be amended"), `qcow2::QcowHeader::parse(&buf)` → return `{ cluster_size, current_version, current_refcount_bits, current_incompatible_features, current_compatible_features, virtual_size }`. Implement `run_amend` per the mission: `parse_amend_o_options` (error already covers the empty case); `probe_amend_target`; build `flags` from the parsed options + `-q`; call `run_amend_guest(core_code, amend_code, probe fields, flags, output_backing, …)` (signature mirrors `run_resize_guest`); on `result.error != AmendResult::ERROR_OK` return `Err(format!("amend: {}", map_amend_error(result.error)))`; else if `result.action == ACTION_AMENDED` re-open the file read+write and `sync_all()`; then `render_amend_success`. Implement `map_amend_error(u32) -> &'static str` covering every `AmendResult::ERROR_*` (unsupported format, invalid option, downgrade-blocked-feature, downgrade-refcount-width, lazy-requires-v3, header-mismatch, parse-failed, dirty, extension-relocation-unsupported, scratch-too-small, internal-overflow, write-failed). Implement `render_amend_success(args, target_format, action, resulting_version, resulting_lazy_refcounts)` mirroring `render_resize_success` (`:4270`): respect `-q`; human prints "Image amended." or "No change." (NoOp); json prints `{filename, format, action ("amended"/"noop"), compat ("0.10"/"1.1" from resulting_version), lazy_refcounts ("on"/"off")}` via `json_escape_string`/`image_format_name`. Leave `run_amend_guest` as an `unimplemented!()`/stub for 4c so this compiles. opus: orchestration + the durability fsync + the error mapping are correctness-bearing. Validate with `pre-commit`/`make test-rust`. |
| 4c | high | opus | none | Implement `run_amend_guest` in `src/vmm/src/main.rs`, modelled closely on `run_resize_guest` (`:4685`). Signature: `(core_code: &[u8], operation_code: &[u8], target_format: u32, flags: u32, cluster_size: u32, current_version: u32, current_refcount_bits: u32, current_incompatible_features: u64, current_compatible_features: u64, virtual_size: u64, output_backing: backing::BackingStore, output_capacity_hint: u64, verbose: bool) -> Result<AmendRunResult, _>`. Copy resize's KVM/VM/memory setup and `write_slice` of core+op. Write `AmendConfig` field-by-field at `OPERATION_CONFIG_ADDR` per the shared layout (magic `0x414D4E44` @0, target_format @4, flags @8, sector_size=512 @12, cluster_size @16, current_version @20, current_refcount_bits @24, _pad @28 stays 0, current_incompatible_features @32, current_compatible_features @40, virtual_size @48, _reserved @56 stays 0). Device set: 1-sector stub input at slot 0 (resize's `ResizeStubInput` temp-file pattern), output_backing (writable) at slot 1. vCPU loop mirroring resize's, but harvest `Payload::AmendResult(a)`: `harvested.action = a.action; harvested.resulting_version = a.resulting_version; harvested.resulting_lazy_refcounts = a.lazy_refcounts as u32; harvested.error = a.error;` (set `target_format` = the host's `target_format` arg, NOT parsed from the echoed string) and set `result_seen`. Load `amend.bin` via `get_binary_path("amend.bin")` + `load_guest_binary` in `run_amend` (4b) and thread it in, mirroring resize's `:4568`. Error "amend: guest did not return a result" if `!result_seen`. opus: the config-write offsets and the harvest must be byte/field exact, and the KVM launch is intricate. Validate with `make instar` (links + builds) and `make test-rust`. |
| 4d | low | sonnet | none | Update `docs/plans/PLAN-amend.md`: mark the phase-4 row status. (No master-plan Open question maps specifically to phase 4 beyond OQ8, already resolved.) Do NOT add this phase file to `order.yml`. Do NOT touch `usage.md`/`CHANGELOG` (phase 9). |
| 4e | low | sonnet | none | From the worktree root: `pre-commit run --all-files`; `make instar` (builds, `core.bin` unchanged, `amend.bin` unchanged); `make test-rust` (all suites incl. the new `parse_amend_o_options` tests). Then stage and present a single commit for steps 4a–4d with the CLAUDE.md message convention. Do not push. (The end-to-end smoke test is run by the management session in review, not this step.) |

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. After each step the management session reads
the actual changed files, confirms no unrelated files changed,
runs the named gates, and then commits/retries/upgrades. The
sandbox **denies direct `cargo`**; validate via `make instar`,
`make test-rust`, `pre-commit run --all-files`. **After 4c lands,
the management session runs the end-to-end smoke test itself**
(build, `instar amend` against qemu fixtures, cross-check with
`qemu-img`) — this is the key verification for the phase.

### Model and effort notes

- 4a (clap + pure `-o` parser + its unit tests), 4d, 4e are
  mechanical/pure; sonnet at medium/low effort.
- 4b (probe + orchestration + fsync + error map) and 4c (the KVM
  launch + config write + harvest) are correctness-bearing and use
  opus. They are interdependent (run_amend calls run_amend_guest)
  and land in one commit.
- When in doubt, skew to the more capable model.

### Management session review checklist

After the steps, and especially after 4c:

- [ ] Read the changed files — don't trust the summary.
- [ ] No unrelated files modified.
- [ ] `AmendConfig` write offsets match the shared struct exactly
      (magic@0 … virtual_size@48); the harvest reads numeric
      action/version and bool lazy, not a parsed format string.
- [ ] `make instar` builds; `core.bin`/`amend.bin` unchanged.
- [ ] `make test-rust` passes incl. `parse_amend_o_options` tests.
- [ ] `pre-commit run --all-files` clean.
- [ ] **End-to-end smoke test (needs `/dev/kvm`)**, using fresh
      `qemu-img` fixtures: (a) `instar amend -o compat=1.1` on a v2
      image → `qemu-img info` shows `compat 1.1`, `qemu-img check`
      passes; (b) `-o compat=0.10` on a v3 image without v3-only
      features → `0.10`, check passes; (c) `-o lazy_refcounts=on`
      then `off` on a v3 image; (d) `-o compat=1.1` on a v2 image
      **with a backing file** → backing reference preserved
      (`qemu-img info` still shows it), check passes; (e) a no-op
      (`-o compat=1.1` on an already-v3 image) prints "No change."
      and exits 0; (f) cross-check each against `qemu-img amend`
      producing an info-equivalent image. Capture any divergence
      for phase 6's `KNOWN_AMEND_DIVERGENCES`.

## Administration and logistics

### Success criteria

Phase 4 is complete when:

* `instar amend [-f qcow2] -o compat=…[,lazy_refcounts=…] [-q]
  [--output json] FILE` parses, probes, launches the guest,
  harvests the result, fsyncs on a successful rewrite, and renders
  human/json; a guest error becomes a non-zero exit with a mapped
  message.
* `parse_amend_o_options` accepts only `compat`/`lazy_refcounts`,
  rejects everything else with a clear message, and is unit-tested.
* `make instar` builds, `core.bin`/`amend.bin` unchanged,
  `make lint`/`make test-rust`/`pre-commit` clean.
* The end-to-end smoke test passes for upgrade, downgrade, lazy
  toggle, backing-file-preserving upgrade, and no-op, cross-checked
  against `qemu-img`.

### Future work created by this phase

- Phase 5: Rust round-trip tests in `src/crates/amend/tests/`.
- Phase 6: Python integration tests (`tests/test_amend.py`) with a
  `KNOWN_AMEND_DIVERGENCES` registry seeded by any divergence the
  phase-4 smoke test surfaces (e.g. instar's `header_length=104` +
  no feature-name-table vs qemu's 112 on upgrade).
- Phase 7: cross-version baselines.
- If the phase-4 smoke test reveals a real bug in the planner or
  guest (phases 2–3), fix it there and note it here.

### Documentation index maintenance

This is a phase plan, not a master plan. It is **not** added to
`docs/plans/order.yml`. The master plan links to it from its
Execution table (already present).

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
