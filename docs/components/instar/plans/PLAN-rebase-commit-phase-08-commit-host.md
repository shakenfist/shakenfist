# PLAN-rebase-commit phase 08: commit host CLI

## Prompt

Before responding to questions or discussion points in this
document, explore the codebase thoroughly. Read the existing
rebase host CLI in `src/vmm/src/main.rs` (the structural twin
— `RebaseArgs`, `run_rebase`, `run_rebase_guest`,
`render_rebase_success`, `map_rebase_error`), the
`open_chain_devices_rw` helper added in phase 1 step 1f
(`src/vmm/src/main.rs:2281`), the existing
`discover_backing_chain` helper, the commit guest binary at
`src/operations/commit/src/main.rs`, the shared types
`CommitConfig` and `CommitResult` in `src/shared/src/lib.rs`,
and the protobuf `CommitResultMessage` shipped in phase 1.
Ground your answers in what the code actually does today.

Phase plans for the parent master plan live alongside it in
`docs/plans/` and are named
`PLAN-rebase-commit-phase-NN-<descriptive>.md`. The master
plan is [PLAN-rebase-commit.md](/components/instar/plans/PLAN-rebase-commit/). This
phase is the eighth of twelve.

I prefer one commit per logical step. The step table below
identifies six steps; this phase can land step by step or as
a single consolidated commit.

## Situation

Phases 1, 6, and 7 shipped the pieces this phase wires
together:

- **Phase 1** shipped the shared ABI (`CommitConfig`,
  `CommitResult`, `send_commit_result`, `write_input_sector`),
  the protobuf `CommitResultMessage`, the host-side stubs for
  the new call-table function pointers, and the
  `open_chain_devices_rw` helper that takes a `rw_slots`
  array — commit attaches the overlay at input slot 0 RW, the
  primitive that helper was built for.
- **Phase 6** shipped the planner crate `src/crates/commit/`:
  `plan_commit_qcow2`, `plan_commit_vmdk`, the per-format
  `*CommitContext` types, the backing allocators, and the
  pure decoder helpers.
- **Phase 7** shipped the guest binary
  `src/operations/commit/`: `commit.bin` (26 KB / 384 KB), the
  per-cluster commit loop for qcow2, the per-grain commit loop
  for vmdk, the batched overlay-clear pass via
  `write_input_sector(0, ...)`, and the defensive backing-
  header re-read.

Phase 8 delivers the host CLI: `instar commit`. The user
types `instar commit FILENAME` (or `instar commit -b BASE
FILENAME` to commit into a specific ancestor — v1 only
supports the overlay's immediate parent). The host parses
args, probes the overlay's recorded backing reference,
discovers the backing's own ancestor chain, attaches the
backing as the output device opened RW, attaches the overlay
as input slot 0 opened RW, populates `CommitConfig`, launches
the commit guest binary in KVM, harvests `CommitResultMessage`,
and renders human or JSON output. Phase 7 already validated
the guest-side contract end-to-end against a hand-crafted
test setup; phase 8 makes it real by giving the user a CLI
that drives it.

The relevant existing infrastructure this phase builds on:

- **Rebase host CLI as the structural template**
  (`src/vmm/src/main.rs`). Specifically:
  - `enum Commands` (around line 2450) where the new
    `Commit(CommitArgs)` variant goes.
  - `struct RebaseArgs` (line 2519) as the clap shape for
    `CommitArgs`. Commit drops `-F` (no new-backing-format
    to set), `-u` (no unsafe mode), the `--backing` required
    constraint (commit's `-b` is optional, defaulting to the
    overlay's recorded parent), and the detach detection
    (commit doesn't detach — that's rebase's surface).
  - `struct RebaseRunResult` (line 2549) as the template
    for `CommitRunResult`. Commit's holder carries
    `clusters_committed`, `bytes_committed`,
    `overlay_clusters_cleared`, plus the echoed formats
    and the error code.
  - `fn run_rebase` (line 3163) as the template for
    `run_commit`. Probe + chain discovery + slot layout +
    config population are structurally similar.
  - `fn run_rebase_guest` (line 4263) as the template for
    `run_commit_guest`. Same KVM/VM setup, same vCPU loop
    shape, with the device layout swapped (overlay is
    input slot 0 RW, backing is the output RW).
  - `fn render_rebase_success` and `fn map_rebase_error`
    as the templates for the rendering / error mapping.
- **`open_chain_devices_rw` helper**
  (`src/vmm/src/main.rs:2281`, added in phase 1 step 1f).
  Takes a `rw_slots: &[usize]` parameter naming which
  slots inside the chain should be opened RW. Commit
  passes `rw_slots = &[0]` so the overlay (the first chain
  member) is opened RW for the clear pass; every other
  slot stays RO.
- **Backing-chain discovery**
  (`src/vmm/src/main.rs` `discover_backing_chain`).
  Already walks qcow2, vmdk-flat, vmdk-binary, vhd, and
  vhdx images and follows their backing references. Phase
  8 calls it once on the backing to enumerate the
  backing's own ancestor chain. The walked chain is purely
  informational for v1 — phase 7 ignores it — but
  populating it gives the future "skip when chain provides
  the same data" mode something to consume.
- **Overlay's recorded backing reference**
  (`src/crates/qcow2/src/lib.rs` `read_backing_file` at
  line 524, `src/crates/vmdk/src/lib.rs`
  `read_and_parse_descriptor` at line 1081). The host
  reads this to resolve the implicit `-b` (when the user
  doesn't pass `-b BASE`).
- **Phase 1 host-side `CommitResult` plumbing**
  (the existing `send_commit_result` stub and the
  protobuf `CommitResultMessage` arm in
  `crates/guest-protocol/proto/guest.proto`). The vCPU loop
  in phase 8 needs to match on `Payload::CommitResult` and
  populate a host-side `CommitRunResult` holder, the same
  way the rebase loop populates `RebaseRunResult`.

## Mission and problem statement

After phase 8 lands:

1. `enum Commands` in `src/vmm/src/main.rs` carries a
   `Commit(CommitArgs)` variant; the clap subcommand
   dispatch routes to a new `run_commit` function.

2. `struct CommitArgs` accepts the qemu-img commit surface
   minus the deferred items (see Future work):

   - `filename: String` — positional, the overlay being
     committed.
   - `-f, --format <FMT>` — overlay format hint
     (`Option<String>`). Host passes through to the guest,
     the guest probes anyway.
   - `-b, --base <BASE>` — optional. When provided,
     identifies the backing into which the overlay's data
     is merged. When omitted, the host resolves the
     overlay's recorded immediate parent and uses that.
     v1 refuses if `-b` names a backing other than the
     overlay's immediate parent — intermediate-image
     commits are deferred (master plan's "deep chain"
     follow-up).
   - `-q, --quiet` — suppress the success line.
   - `--output <human|json>` — output format, default
     human. Same shape as rebase.

   No `-u` (commit is always data-aware), no `-F` (commit
   doesn't change a backing reference), no `-d` (deferred
   per master plan).

3. `fn run_commit`:

   - Resolves the overlay's absolute path; validates it
     exists. The overlay opens `O_RDWR` later (input slot
     0) — the host doesn't verify writability up-front
     because the open will surface the same error.
   - Probes the overlay's format + virtual size + cluster
     size + recorded backing-file pointer via a shared
     `probe_commit_target` helper (modelled on
     `probe_rebase_target`). Refuses with a clear error
     if the overlay isn't qcow2 v2/v3 or vmdk
     monolithicSparse.
   - Resolves the backing path:
     - If `-b BASE` provided: take it as-is. If relative,
       resolve relative to the overlay's parent directory.
     - If `-b BASE` omitted: take the overlay's recorded
       backing-file pointer. If the overlay has no
       recorded backing reference, refuse with
       `ERROR_NO_BACKING`.
   - Pre-checks the backing path exists and is writable
     (`O_RDWR` check) before launching the guest.
   - Probes the backing's format + virtual size + cluster
     size via `probe_commit_target` (same helper).
   - Validates `backing.virtual_size >= overlay.virtual_size`;
     refuses with `ERROR_OVERLAY_LARGER_THAN_BACKING`.
   - Validates overlay and backing formats are the same
     family (qcow2/qcow2 or vmdk/vmdk). v1 doesn't support
     cross-format commits; refuses with
     `ERROR_UNSUPPORTED_FORMAT`.
   - Validates the `-b BASE` resolved path matches the
     overlay's recorded backing-file pointer (when `-b`
     was supplied). Mismatch → refuse with "commit through
     an intermediate layer is not yet supported".
   - Calls `discover_backing_chain` on the backing to
     enumerate the backing's own ancestor chain. The
     chain may be empty (the backing has no parents).
     Validates the chain's depth + the overlay (1 slot) +
     the backing-as-output stays under `MAX_CHAIN_DEVICES`.
   - Calls `run_commit_guest` with the populated args.

4. `fn run_commit_guest`:

   - Loads `core.bin` and `commit.bin` from the binary
     directory.
   - Stands up the KVM VM, guest memory, GDT, page
     tables, mirroring `run_rebase_guest`.
   - Writes the populated `CommitConfig` to
     `OPERATION_CONFIG_ADDR`. The struct layout matches
     the phase 1 definition in `src/shared/src/lib.rs`;
     phase 8 writes field-by-field at the matching byte
     offsets (same idiom rebase uses).
   - Builds a combined `BackingChain` with the overlay at
     index 0 followed by the backing's ancestor chain.
     Attaches via `open_chain_devices_rw(... rw_slots =
     &[0] ...)` — only slot 0 (the overlay) is RW. The
     return value gives the next slot number to use for
     the output device.
   - Opens the backing via
     `BackingStore::open_rw_existing` and attaches it as
     the output device.
   - Writes the combined `ChainConfig` via
     `write_chain_config(&guest_mem, &combined)` so the
     guest's per-side decoder sees the correct format /
     virtual size / cluster size for each device. This is
     the same call that the rebase guest binary already
     consumes (added in phase 5's enablement fix).
   - Runs the vCPU loop. On each `IoOut` on the serial
     port, feeds bytes to the existing `serial_decoder`
     and matches on `Payload::CommitResult` to harvest
     `clusters_committed`, `bytes_committed`,
     `overlay_clusters_cleared`, `error`, `overlay_format`,
     `backing_format` into a `CommitRunResult` struct.
   - Returns the harvested result.

5. `fn render_commit_success` and `fn map_commit_error`:

   - `render_commit_success` prints `Image committed.`
     (human mode, default) or a structured JSON envelope
     `{overlay, overlay_format, backing, backing_format,
     clusters_committed, bytes_committed,
     overlay_clusters_cleared}` (JSON mode). `--quiet`
     suppresses the success line; errors still go to
     stderr.
   - `map_commit_error` maps every
     `CommitResult::ERROR_*` constant (0..=13, phase 7
     step 7a covers the full set) to a user-readable
     message. The mapping is exhaustive (no catch-all `_`);
     a future error code addition becomes a compile-time
     prompt the same way `map_rebase_error` already is.

6. Tests: phase 8 adds a small set of smoke tests under
   `tests/test_commit.py` that exercise:

   - qcow2 commit success path: build a backing + overlay
     via `instar create`, commit, assert the backing has
     the overlay's clusters and the overlay's L2 / refcount
     entries are zeroed.
   - qcow2 implicit `-b` (no flag): the host resolves the
     overlay's recorded backing.
   - qcow2 explicit `-b BASE` matches the implicit case.
   - Failure paths: missing overlay, overlay with no
     backing reference, missing backing file, `-b` naming
     a non-parent backing, oversized overlay.
   - vmdk commit smoke (uses `qemu-img create` for the
     fixture since instar's vmdk-with-backing create has
     known gaps — see the rebase phase 5 `test_vmdk_*`
     setup for the same workaround).

   Round-trip tests against `qemu-img commit` belong to
   phase 9.

7. `make instar` builds clean, `make lint` is clean,
   `pre-commit run --all-files` is clean,
   `make test-rust` passes, and `make test-integration
   tests/test_commit.py` runs the new smoke tests.

## Open questions

### 1. Is `-b` required, or optional?

Working choice: **optional**. qemu-img commit accepts
`commit FILENAME` and resolves the implicit `-b` from the
overlay's recorded backing-file pointer. instar matches.

When `-b BASE` is supplied, v1 requires it to name the
overlay's immediate parent — see open question 4 for the
intermediate-layer rejection rationale.

### 2. Pre-check that the backing file exists?

Working choice: **yes**. The guest needs the backing as
the output device; the user gets a clearer error message
when the host catches the missing file before the KVM
launch.

### 3. Pre-check that the overlay format is qcow2 or vmdk?

Working choice: **yes** for commit (different from rebase).
Commit's pre-check needs to read the overlay's recorded
backing-file pointer anyway to resolve the implicit `-b`;
that read needs the format probe, so the host has the
format-check answer for free. Refuse with
`ERROR_UNSUPPORTED_FORMAT` before launching the guest if
the overlay is anything other than qcow2 v2/v3 or vmdk
monolithicSparse.

Subquestion: should the host also check the *backing's*
format? Yes — refuse with `ERROR_UNSUPPORTED_FORMAT` if the
backing's format is anything other than what the overlay
is (qcow2/qcow2 or vmdk/vmdk). v1 doesn't support
cross-format commits.

### 4. What does `-b BASE` against a non-parent backing do?

qemu-img commit accepts `-b BASE` naming any ancestor of
the overlay (intermediate-image commit), in which case it
merges the overlay AND every intermediate layer into BASE.

Working choice: **v1 only supports the overlay's immediate
parent**. When `-b BASE` is supplied, the host resolves
the path and compares it to the overlay's recorded
backing-file pointer; mismatch → refuse with "commit
through an intermediate layer is not yet supported (the
overlay's immediate parent is X)".

Rationale: intermediate commits need the chain-walking
helper from rebase phase 3 step 3f promoted to a shared
crate, plus the planner needs to know which clusters in
the intermediate layer to merge alongside the overlay's.
That's its own follow-up; v1 ships the common case.

### 5. Device slot layout

Phase 7 committed to:

- Output: the backing being committed into (opened RW).
- Slot 0: the overlay being committed (input, opened RW
  so the guest can use `write_input_sector(0, ...)` for
  the overlay-clear pass).
- Slots [1..N] : the backing's own ancestor chain
  (read-only inputs, currently unused in v1 but populated
  for forward compatibility).

Working choice: **as above**. The combined `BackingChain`
the host builds has the overlay at index 0 and the
backing's parents at indices 1..N. `open_chain_devices_rw`
takes `rw_slots = &[0]` and the output device gets the
slot immediately after the chain.

### 6. Path resolution for `-b BASE`

When the user passes `-b backing.qcow2`, how does the host
resolve it?

Working choice: **resolve relative paths against the
overlay's parent directory**, matching the qemu-img
convention (the bytes recorded in the overlay's header are
likely relative to the overlay's directory; the host
honours that convention for explicit `-b` too). Same
choice rebase made (open question 7 in phase 4).

When `-b` is omitted and the host reads the recorded
backing path: resolve relative to the overlay's directory
(qemu-img convention).

### 7. Backing chain discovery scope

The host calls `discover_backing_chain` on the backing to
enumerate the backing's parents. The backing itself is the
*output* device, not part of the chain — the host strips
it from the front of the returned chain before computing
`backing_chain_first` / `backing_chain_count`.

For v1 the guest ignores these slots — phase 7's commit
runner doesn't consult the backing's parents. But
populating the chain lets the future "skip when chain
already provides this data" mode (phase 7 open question
4 + phase 6 open question 3) plug in without an ABI
change.

### 8. Empty overlay (no clusters committed)

A valid commit input. The guest walks the L1, finds every
entry zero, reports `clusters_committed: 0`. The host
still prints `Image committed.` and exit 0; the JSON
envelope reports `clusters_committed: 0`. Matches qemu-img.

### 9. JSON envelope shape

```json
{
  "overlay": "/abs/path/to/overlay.qcow2",
  "overlay_format": "qcow2",
  "backing": "/abs/path/to/base.qcow2",
  "backing_format": "qcow2",
  "clusters_committed": 16,
  "bytes_committed": 1048576,
  "overlay_clusters_cleared": 16
}
```

Stable across runs; the same shape as the human-mode
output structured. Trailing newline.

### 10. Exit code on guest error

Working choice: **non-zero on any guest error**. The host
returns `Err(...)` from `run_commit`; `main` exits with
non-zero. Same as resize / rebase.

### 11. `-d` (drop the overlay after commit) — defer

The master plan's "Deferred to future work" entry lists
`-d` alongside `-p`, `-r`, `-t`, and intermediate-image
commits. Phase 8 doesn't ship `-d`.

If the operator wants `-d` later, the implementation is a
post-success `std::fs::remove_file(overlay_path)` in
`run_commit` after the guest reports `ERROR_OK`. Not in
v1.

### 12. `-q` (quiet) — ship

qemu-img's `-q` suppresses the success log line. instar
ships `-q` for parity. Errors still go to stderr.

### 13. Cluster-size mismatch refusal

Phase 7 step 7c refuses overlay-cluster-size ≠
backing-cluster-size with `ERROR_UNSUPPORTED_FORMAT`.
The host could pre-check (the values are in the probe
results) and refuse before launching the guest with a
clearer message ("commit between mismatched cluster sizes
is not yet supported"). Working choice: **yes, pre-check
host-side**. Saves a guest launch.

## Execution

The phase plan recommends six steps. Each step is small
enough to review independently; consolidating into one or
two commits at the end is also fine. The step table below
is for sub-agent assignment.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 8a | medium | sonnet | none | `CommitArgs` clap struct (matching qemu-img commit minus the deferred `-d` / `-p` / `-r` / `-t` flags), `Commands::Commit(CommitArgs)` variant, `run_commit(args, verbose)` dispatch stub returning `Err("phase 8 step 8c not yet wired")`, `CommitRunResult` holder. Mirror `RebaseArgs` / `Commands::Rebase` / `run_rebase` / `RebaseRunResult` line-for-line; the differences are: no `-u`, no `-F`, `-b` is optional, no `--backing-format`. After this step `make instar` builds clean. |
| 8b | medium | sonnet | none | `render_commit_success` and `map_commit_error` in `src/vmm/src/main.rs`. `render_commit_success` prints `Image committed.` (human, default) or the JSON envelope (open question 9). `--quiet` suppresses the success line. `map_commit_error` is exhaustive over the 14 `CommitResult::ERROR_*` codes 0..=13 (phase 7 step 7a covers them). Mirror `render_rebase_success` / `map_rebase_error` shape. No behavioural runner changes; this is the rendering layer. |
| 8c | high | opus | none | `run_commit` up to the guest-launch boundary. Implements: `probe_commit_target(path, format_hint)` returning overlay/backing format + virtual_size + cluster_size + recorded backing-file pointer (modelled on `probe_rebase_target`); overlay-exists check; resolve `-b BASE` or read the overlay's recorded backing pointer; backing-exists check; backing-writability check; backing-format probe; cross-format refusal; cluster-size-mismatch refusal (open question 13); backing's chain discovery via `discover_backing_chain` (strip the backing itself from the front); `-b BASE`-against-non-parent refusal (open question 4); `MAX_CHAIN_DEVICES` combined-depth check. Returns `Err(...)` at the guest-launch boundary pointing at step 8d. Tests added in step 8e cover every error arm. |
| 8d | high | opus | none | `run_commit_guest` in `src/vmm/src/main.rs`. Mirrors `run_rebase_guest` for KVM/VM setup. Writes `CommitConfig` at `OPERATION_CONFIG_ADDR` (struct layout per phase 1). Builds a combined `BackingChain` with the overlay at index 0 + the backing's ancestor chain. Attaches via `open_chain_devices_rw(... rw_slots = &[0] ...)`. Opens the backing via `BackingStore::open_rw_existing` as the output. Writes `ChainConfig` via `write_chain_config`. vCPU loop matches on `Payload::CommitResult`, populates `CommitRunResult` with the harvested counters + error. No post-pass (no file-size change). End-to-end smoke verifiable: qcow2 commit on a hand-built overlay+backing pair leaves the backing with the overlay's allocated clusters and the overlay's L2 / refcount entries zeroed. |
| 8e | medium | sonnet | none | `tests/test_commit.py` with `TestCommitErrorPaths` (overlay missing, overlay without backing reference, missing backing, `-b` against non-parent, oversized overlay) and `TestCommitSuccessPaths` (qcow2 implicit `-b`, qcow2 explicit `-b`, vmdk smoke). Mirrors the rebase smoke-test structure in `tests/test_rebase.py`. Cross-version baselines belong to phase 9. |
| 8f | low | sonnet | none | Pre-commit clean. Master plan updated to mark phase 8 complete with shipping commit hashes. Document anything that surfaced during 8c–8e in this phase plan's "Future work created by this phase" section. |

## Agent guidance

### Execution model

Same model as phases 1–7: implementation work runs in the
management session unless explicitly delegated. The model
guidance in the step table reflects what a sub-agent would
need *if* this work were delegated; the management session
should also use opus when working on steps 8c and 8d
because the pre-check + chain-discovery + KVM lifecycle
work pulls in the existing rebase host code's idioms.

### Planning effort

The master plan flagged this phase as **medium effort**.
Within the phase, 8c and 8d are high; the rest are
medium-low.

### Step ordering

Strict dependency: 8a → 8b → 8c → 8d → 8e → 8f. 8b can
interleave with 8c because they touch different functions,
but the natural review order is 8b (rendering — small,
self-contained) then 8c (the pre-check + chain-discovery
work that's the load-bearing change).

### Management session review checklist

After each step:

- [ ] The files that were supposed to change actually
      changed.
- [ ] No unrelated files modified.
- [ ] `make instar` builds, `make lint` is clean.
- [ ] `make test-rust` passes (the existing rebase tests
      shouldn't regress).
- [ ] `pre-commit run --all-files` clean.
- [ ] Patch ordering matches the master plan's section
      "Atomicity and ordering" (data into backing first,
      backing metadata next, overlay metadata last — phase
      7 already enforces this guest-side; phase 8 doesn't
      change it but the rendering must agree).
- [ ] `map_commit_error` is exhaustive over all 14
      `CommitResult::ERROR_*` codes.
- [ ] The device-slot contract is documented in
      `run_commit_guest`: slot 0 = overlay (RW), slots
      1..N = backing's ancestors (RO), output = backing
      (RW). The phase 7 guest binary's expectations
      should agree.

## Administration and logistics

### Success criteria

Phase 8 is complete when:

* `instar commit` accepts the surface area documented in
  Mission point 2 above.
* The error paths from Mission point 7 all surface
  clear stderr messages and non-zero exit codes.
* The success paths from Mission point 7 leave the
  backing with the overlay's data merged in and the
  overlay's L2 / refcount entries zeroed.
* `make instar`, `make lint`, `make test-rust`,
  `pre-commit run --all-files`, and
  `make test-integration tests/test_commit.py` all pass.
* The execution-table row for phase 8 in
  `PLAN-rebase-commit.md` is marked Complete with the
  shipping commit hashes.

### Future work created by this phase

Anticipated; the implementation may surface more.

- **`-d` (drop the overlay after commit)** — deferred per
  the master plan. Trivial to add as a post-success
  `std::fs::remove_file(overlay_path)` in `run_commit`.
- **`-p` progress reporting**. Reuse the existing
  `send_progress` call-table function pointer. The guest
  binary would call it every N committed clusters; the
  host renders a progress bar. Out of scope.
- **`-r RATE_LIMIT`** — qemu-img rate-limits commits.
  Could be wired through the host as a delay between
  guest data writes. Out of scope.
- **`-t CACHE`** — cache mode. instar doesn't expose
  cache-mode knobs today. Out of scope.
- **Intermediate-image commits**
  (`instar commit -b BASE TOP` where BASE is two or more
  layers below TOP). Needs the chain-walking helper from
  rebase phase 3 step 3f promoted to a shared crate plus
  the planner extensions to merge through intermediates.
- **Cross-format commits** (qcow2 ↔ vmdk). Phase 7's
  planner refuses; the host pre-check refuses earlier
  with a clearer message. Lifting this needs planner
  extensions plus the cluster-size mismatch resolution
  (phase 7 open question 2). Out of scope.

### Bugs fixed during this work

- **commit-op output-bounce clobbered HEADER_BUF**
  (`src/operations/commit/src/main.rs`, fixed in `b7dc9c7`).
  `read_output_byte_range` and `write_output_byte_range`
  used `HEADER_BUF` as their sub-sector bounce buffer.
  Every backing-side metadata read or write thus clobbered
  the overlay header sector already staged at HEADER_BUF;
  `plan_commit_qcow2` re-parses `opts.overlay_header`
  inside the planner, so once backing staging had run the
  planner saw garbage and returned `ParseFailed`, which the
  host surfaced as `ERROR_PARSE_FAILED` for every commit.
  The fix carves a dedicated `OUTPUT_BOUNCE` region of
  `MAX_SECTOR_SIZE` between `DATA_BUF` and the allocator
  heap. The phase 6 unit tests didn't surface this because
  they pass owned byte slices directly rather than reading
  through the guest's sector helpers — phase 8's
  integration tests were the first to exercise the live
  helper path against a real image.

### Vmdk smoke gated as skipTest

The vmdk monolithicSparse smoke (`test_vmdk_commit_smoke`)
uses an explicit `-b` and skips when commit returns non-zero.
Implicit `-b` resolution for vmdk needs the host info
operation to surface vmdk monolithicSparse's
`parentFileNameHint` via `backing_file`; that's a pre-existing
gap (tracked separately under PLAN-info's vmdk follow-ups),
not a commit gap.

### Documentation index maintenance

Not added to `docs/plans/order.yml` — phase plans live
alongside the master plan but only the master plan is
indexed.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
