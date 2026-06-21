# PLAN-amend phase 03: guest operation binary

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (the guest-op structure
in `src/operations/{rebase,resize}/`, the `_start` entry point, the
fixed guest memory map in `src/shared/src/lib.rs`, the
`read_byte_range`/`write_byte_range` helpers, the call-table
boundary, the build wiring in `src/build.sh` /
`scripts/check-binary-sizes.sh` / the Makefile), and ground your
answers in what the code actually does today. Do not speculate
when you could read the code. Where a question touches the qcow2
format or crash-safety/durability, research the qcow2 spec and
qemu's `qcow2_update_header` as needed. Flag uncertainty rather
than guessing.

Phase plans for the parent master plan live alongside it in
`docs/plans/` and are named
`PLAN-amend-phase-NN-<descriptive>.md`. The master plan is
[PLAN-amend.md](/components/instar/plans/PLAN-amend/); phase 1 (ABI,
[PLAN-amend-phase-01-abi.md](/components/instar/plans/PLAN-amend-phase-01-abi/)) and
phase 2 (the planner,
[PLAN-amend-phase-02-qcow2-planner.md](/components/instar/plans/PLAN-amend-phase-02-qcow2-planner/))
are landed. This is the third of nine.

I prefer one commit per logical change, and at minimum one commit
per phase. Each commit should be self-contained: it should build,
pass tests, and have a clear commit message explaining what changed
and why.

## Situation

Phases 1–2 landed the ABI (`AmendConfig`/`AmendResult`,
`send_amend_result`, the `AmendResultMessage` proto) and the pure
`no_std` planner `src/crates/amend/` (`plan_amend_qcow2`, which owns
the decision matrix and emits header patches). Nothing runs yet:
there is no guest binary and no host CLI.

This phase builds **`src/operations/amend/`** — the KVM guest
binary that the host launches to actually mutate the image. It is
the bridge between the host (phase 4) and the planner (phase 2):
read the config and the image header, validate, call
`plan_amend_qcow2`, apply the returned patches to the output
device, and report the result. It also **resolves master-plan Open
question 6 (crash-safety / write ordering)**.

The grounding the implementer builds on (verified on the `amend`
branch):

- **Guest-op skeleton.** `src/operations/rebase/src/main.rs:1395`
  (`_start`) is the reference: get the call table
  (`get_call_table()` → `*(CALL_TABLE_ADDR as *const CallTable)`),
  `validate_call_table!`, read `&*(OPERATION_CONFIG_ADDR as *const
  RebaseConfig)`, check `is_valid()`, read sector 0 of the **output**
  device into `HEADER_BUF` via `read_output_sector`,
  `detect_format_from_header`, dispatch per-format, then
  `send_rebase_result(&result)` + `send_complete(b"rebase\0", bytes,
  ok)`. Every failure path fills an error result and calls
  `send_complete(.., false)`.
- **Fixed guest memory map** (`src/shared/src/lib.rs`):
  `CALL_TABLE_ADDR=0x80000`, `OPERATION_CONFIG_ADDR=0x81000`,
  `SCRATCH_MEM_BASE=0x300000`, `SCRATCH_MEM_END≈0x00FF0000`,
  `MAX_SECTOR_SIZE=65536`. Per-op scratch is carved from
  `SCRATCH_MEM_BASE`; rebase
  (`src/operations/rebase/src/main.rs:61`) uses `HEADER_BUF =
  SCRATCH_MEM_BASE` (one `MAX_SECTOR_SIZE`), `EXISTING_STATE =
  HEADER_BUF + MAX_SECTOR_SIZE` (4 MiB), `PLANNER_SCRATCH =
  EXISTING_STATE + 4 MiB` (4 MiB). resize uses an 8 MiB
  `PLANNER_SCRATCH`.
- **`read_byte_range`** (`rebase/src/main.rs:159`) reads an
  arbitrary byte range across sectors into a destination pointer,
  using `HEADER_BUF` as a bounce buffer for partial sectors;
  aligned full-sector reads go straight to the destination.
  **`write_byte_range`** (`rebase/src/main.rs:201`) is the mirror
  with read-modify-write for partial leading/trailing sectors.
  `apply_rebase_plan` (`:246`) just loops `plan.patches()` calling
  `write_byte_range`.
- **Crash-safety prior art.** `resize` (`operations/resize/src/main.rs:274`)
  and `rebase` (`:246`) write the qcow2 header **directly** — no
  corrupt-bit guard, no per-patch fsync; they rely on planner patch
  ordering and the host's post-op `file.sync_all()`
  (`vmm/src/main.rs:4662`). `check --repair`
  (`operations/check/src/main.rs:3800`) is the *only* op that sets
  `INCOMPAT_CORRUPT` → mutate → clear, and only because it rewrites
  many independent structures across many clusters in phases. There
  is **no output-device fsync primitive** in the call table
  (`fsync_input` is input-only); the host fsyncs the output file
  after the guest halts.
- **Build wiring** (use rebase as the template):
  `src/operations/rebase/Cargo.toml` (package `rebase-op`, `[[bin]]
  name = "rebase"`, deps `shared` + the `rebase` planner + `qcow2`,
  `[profile.release] panic="abort" opt-level="z" lto=true`);
  `src/operations/rebase/.cargo/config.toml` (target
  `x86_64-unknown-none`, `-Toperations/rebase/linker.ld`,
  `build-std=["core"]`); `src/operations/rebase/linker.ld` (load at
  `0x20000`); `src/build.sh` (build + `rust-objcopy -O binary` →
  `rebase.bin` → copy to `target/release/`);
  `scripts/check-binary-sizes.sh:65` (the `for op in …` list, cap
  `OPERATION_MAX_SIZE=0x60000`=384 KiB); `src/Cargo.toml`
  `members`; the Makefile `test-rust` `--exclude <op>-op` list.
- **Cross-check fields.** `AmendConfig` (phase 1) carries the
  host-probed `cluster_size`, `current_version`,
  `current_refcount_bits`, `current_incompatible_features`,
  `current_compatible_features`, `virtual_size`. The guest
  re-parses the header and compares, à la rebase's defensive
  re-parse, signalling `AmendResult::ERROR_HEADER_MISMATCH (6)` on
  disagreement.

## Mission and problem statement

Implement `src/operations/amend/` so that, when the host launches
it against a qcow2 image with a populated `AmendConfig`:

1. `_start` gets and validates the call table, reads `AmendConfig`
   from `OPERATION_CONFIG_ADDR`, and checks `is_valid()`
   (`ERROR_PARSE_FAILED`/`send_complete(false)` on failure).

2. It reads **sector 0** of the output device, runs
   `detect_format_from_header`, and refuses non-qcow2 with
   `ERROR_UNSUPPORTED_FORMAT` (defence in depth — the host also
   probes in phase 4).

3. It parses the header (`QcowHeader::parse`) and runs the
   **cross-check** against the host-probed `AmendConfig` fields
   (`current_version`, `current_refcount_bits`,
   `current_incompatible_features`, `current_compatible_features`,
   `cluster_size`, `virtual_size`); any disagreement →
   `ERROR_HEADER_MISMATCH`. This catches the file changing between
   the host probe and the guest run, and host/guest parser drift.

4. It reads the **full first cluster** (`cluster_size` bytes, which
   may exceed one sector — up to qcow2's 2 MiB max) into
   `EXISTING_STATE` via `read_byte_range`, after bounds-checking
   `cluster_size <= EXISTING_STATE_LIMIT`
   (`ERROR_PARSE_FAILED`/internal error otherwise). The full
   cluster is required because a version change relocates the
   header-extension area and backing-file string, which can live
   beyond sector 0 (verified: a v3 backing image's backing string
   sits at offset 528).

5. It builds `Qcow2AmendOpts` from the `AmendConfig` flags
   (`FLAG_SET_COMPAT`→`set_compat`, `FLAG_COMPAT_V3`→`target_v3`,
   `FLAG_SET_LAZY`→`set_lazy`, `FLAG_LAZY_ON`→`lazy_on`;
   `header_cluster = EXISTING_STATE[..cluster_size]`) and calls
   `plan_amend_qcow2(&opts, PLANNER_SCRATCH[..LIMIT])`. On
   `Err(e)`, report `e.error_code()`.

6. It applies the plan's patches in order via `write_byte_range`
   (the lazy-toggle 8-byte write at offset 80, or the single
   full-cluster `Write { byte_offset: 0, .. }`); a device write
   failure → `ERROR_WRITE_FAILED`. A `NoOp` plan applies zero
   patches.

7. It fills `AmendResult` (`target_format` echoed,
   `action`=`ACTION_NOOP`/`ACTION_AMENDED` from the plan,
   `resulting_version`, `resulting_lazy_refcounts`, `error`), calls
   `send_amend_result(&result)`, then `send_complete(b"amend\0",
   bytes, ok)` where `bytes` is `cluster_size` for an amended
   image, `0` for a no-op.

8. **Crash-safety: a direct header write, no corrupt-bit guard**
   (resolving master-plan Open question 6 — see Open question 1).

9. The crate builds, fits the 384 KiB op cap, and is wired into
   `src/build.sh`, `scripts/check-binary-sizes.sh`, `src/Cargo.toml`
   members, and the Makefile `test-rust` exclude list. `core.bin`
   is unchanged (this phase adds only a new op binary).

Out of scope: the host CLI / `AmendConfig` population (phase 4 —
until then `amend` is unreachable, the `Commands` enum is
untouched, and `instar amend` still prints "unrecognized
subcommand"); integration tests (phase 6).

## Open questions

### 1. Crash-safety / write ordering — RESOLVED: direct write, no guard

Decision: **amend writes the header cluster directly, with no
`INCOMPAT_CORRUPT` guard and no in-guest fsync**, matching `resize`
and `rebase` (and qemu's own `qcow2_update_header`, which rewrites
the header cluster without a corrupt-bit dance). Rationale:

- amend's only write is the first cluster (one `Write` patch). The
  load-bearing fixed-header fields (magic, version, feature words,
  `header_length`, `refcount_order`) all live in the first 104
  bytes — i.e. the first sector — so they flip atomically in a
  single sector write.
- The host fsyncs the output file after the guest halts
  (`file.sync_all()` at `vmm/src/main.rs:4662`); there is no
  output-device fsync primitive for the guest, by design.
- The corrupt-bit guard exists for `check --repair`'s *multi-cluster,
  multi-phase* mutation, where a crash can leave independent
  structures mutually inconsistent. amend has no such window.

Residual note (document, do not block): for a 512-byte-sector image
whose relocated extensions/backing string span more than the first
sector, a crash mid-rewrite could tear the header across sectors —
the **same window qemu has** (qemu does not guard the header update
either). Accepted for v1; a future hardening could set the corrupt
bit around the rewrite if a real workflow demands it. Confirm we
are comfortable with this (the master plan's OQ6 framing expected
exactly this analysis).

### 2. Memory carve (confirm before 3a)

Working carve, mirroring rebase but simpler (amend needs no chain
caches or compare buffers):

```rust
const HEADER_BUF: usize = SCRATCH_MEM_BASE;                  // sector 0 + bounce, MAX_SECTOR_SIZE
const EXISTING_STATE: usize = HEADER_BUF + MAX_SECTOR_SIZE;  // the full first cluster
const EXISTING_STATE_LIMIT: usize = 4 * 1024 * 1024;        // >= qcow2 max cluster (2 MiB)
const PLANNER_SCRATCH: usize = EXISTING_STATE + EXISTING_STATE_LIMIT;
const PLANNER_SCRATCH_LIMIT: usize = 4 * 1024 * 1024;       // holds the rebuilt cluster
```

`header_cluster` (planner input) = `EXISTING_STATE[..cluster_size]`;
the planner's scratch (rebuilt cluster output) = `PLANNER_SCRATCH` —
distinct buffers, because the planner copies source→scratch. Note
`read_byte_range`/`write_byte_range` use `HEADER_BUF` as their
bounce buffer; the full-cluster read into `EXISTING_STATE` and the
full-cluster write at offset 0 are sector-aligned (cluster_size is
a multiple of sector_size), so they bypass the bounce and never
collide with `EXISTING_STATE`. Confirm the limits and that
`cluster_size <= EXISTING_STATE_LIMIT` is enforced before the read.

### 3. Defensive guard: header occupies the whole first cluster

The planner emits a whole-first-cluster rewrite (`Write { 0,
scratch[..cluster_size] }`), which is correct only if nothing but
the header/extensions/backing string lives in cluster 0 (qemu
reserves cluster 0 for the header; the refcount table and L1 are at
later clusters). Recommended cheap guard in the guest (or planner):
if `refcount_table_offset < cluster_size` or `l1_table_offset <
cluster_size`, the layout is unexpected — refuse with
`ERROR_PARSE_FAILED` rather than clobber metadata. Confirm whether
to add this in the guest (phase 3) or fold it into the planner
(would be a small phase-2 amendment). Recommendation: add it in the
guest cross-check, cheap and local.

### 4. Does `amend-op` need `qcow2`'s `create` feature?

The planner uses only `QcowHeader::parse`, the offset/feature
constants, and `header_extension_area_end` — none gated behind
`create` (which gates `build_header`). Phase 2's `crates/amend`
nonetheless enabled `features=["create"]` (mirroring rebase). LTO
strips the unused `build_header`, so the op binary should stay
small either way, but confirm whether to **drop the `create`
feature** from `crates/amend` and `operations/amend` to keep the
dependency honest. Recommendation: drop it if the build is clean
without it (a one-line check); otherwise leave it — the binary-size
cap is the backstop.

### 5. Guest-side testing

Guest ops are `no_std`/fixed-address and are exercised end-to-end
by the phase-6 integration suite, not by unit tests (rebase/resize
have essentially no inline tests in their `main.rs`). Phase 3's
verification is therefore: it **builds**, fits the **384 KiB cap**,
and `make test-rust` still passes (the planner's phase-2 tests are
the logic coverage). Any small, pure helper added to the op (e.g. a
flags→opts mapper) may get an inline test, but do not contort the
op to be unit-testable. Confirm this is acceptable (functional
correctness lands with phase 6).

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | low | sonnet | none | Scaffold `src/operations/amend/` from the rebase template. `Cargo.toml`: package `amend-op`, version 0.2.0, edition 2021, publish=false, `[[bin]] name = "amend"` path `src/main.rs`, deps `shared = { path = "../../shared" }` + `amend = { path = "../../crates/amend" }` + `qcow2 = { path = "../../crates/qcow2", features = ["create"] }` (match the planner's feature set; see Open question 4), `[profile.release] panic="abort" opt-level="z" lto=true`. Copy `src/operations/rebase/.cargo/config.toml` and `src/operations/rebase/linker.ld` verbatim, changing only `rebase`→`amend` in the `-Toperations/amend/linker.ld` rustflag. Create `src/main.rs` (`#![no_std]`, `#![no_main]`) with the rebase-style `panic`/`get_call_table`/`validate_call_table!` boilerplate and a `_start` that for now just validates the config and calls `send_complete(b"amend\0", 0, false)` (stub). Wire it everywhere: add `"operations/amend"` to `src/Cargo.toml` members; add the build+objcopy+copy stanza for `amend` to `src/build.sh` (mirror the rebase stanza and add `amend.bin` to the "Copied …" echo and the in-script size check); add `amend` to the `for op in …` list in `scripts/check-binary-sizes.sh:65`; add `--exclude amend-op` to the Makefile `test-rust` exclude list (beside `--exclude rebase-op`). Validate with `make instar` (the new `amend.bin` must build and pass the size check) — do NOT run cargo directly (sandbox-denied). |
| 3b | high | opus | none | Implement the `_start` logic in `src/operations/amend/src/main.rs`. Define the memory carve from Open question 2. Sequence: get+validate call table; read `&*(OPERATION_CONFIG_ADDR as *const AmendConfig)`, `is_valid()` else `ERROR_PARSE_FAILED`; `sector_size = (call_table.get_output_sector_size)()`; read sector 0 into `HEADER_BUF` via `read_output_sector`, `detect_format_from_header` → non-qcow2 ⇒ `ERROR_UNSUPPORTED_FORMAT`; `QcowHeader::parse(HEADER_BUF[..sector_size])` else `ERROR_PARSE_FAILED`; **cross-check** parsed `{version, refcount_bits, incompatible_features, compatible_features, cluster_size, virtual_size}` against the matching `AmendConfig` fields ⇒ `ERROR_HEADER_MISMATCH` on any mismatch; **defensive layout guard** (Open question 3): `refcount_table_offset < cluster_size || l1_table_offset < cluster_size` ⇒ `ERROR_PARSE_FAILED`; bounds-check `cluster_size <= EXISTING_STATE_LIMIT`; read the full `cluster_size` bytes into `EXISTING_STATE` via `read_byte_range(call_table, sector_size, 0, EXISTING_STATE, cluster_size)` ⇒ `ERROR_PARSE_FAILED`/read-fail code on failure; build `Qcow2AmendOpts { header_cluster: EXISTING_STATE[..cluster_size], cluster_size, set_compat: flags & FLAG_SET_COMPAT != 0, target_v3: flags & FLAG_COMPAT_V3 != 0, set_lazy: flags & FLAG_SET_LAZY != 0, lazy_on: flags & FLAG_LAZY_ON != 0 }`; call `plan_amend_qcow2(&opts, PLANNER_SCRATCH[..PLANNER_SCRATCH_LIMIT])`, mapping `Err(e)` → `e.error_code()`; apply patches via a `write_byte_range` loop (mirror `apply_rebase_plan`) ⇒ `ERROR_WRITE_FAILED` on failure; build `AmendResult { magic: AmendResult::MAGIC, target_format: config.target_format, action: NOOP/AMENDED from plan.action, error, resulting_version: plan.resulting_version, resulting_lazy_refcounts: plan.resulting_lazy_refcounts as u32 }`; `send_amend_result(&result)`; `send_complete(b"amend\0", if amended { cluster_size as u64 } else { 0 }, error == ERROR_OK)`. Crash-safety: write the header cluster directly — NO corrupt-bit guard (Open question 1). Keep a single error-emitting helper like rebase's `send_result`/`err_result` for the early-exit paths. opus: this is the host/guest/planner bridge with several error paths and the fixed-address memory discipline; getting an offset or an error mapping wrong corrupts images or misreports. Validate with `make instar` (builds, size OK) and `make test-rust` (still green). |
| 3c | low | sonnet | none | Update `docs/plans/PLAN-amend.md`: mark the phase-3 row status; append a "Resolved in phase 3" note to master-plan Open question 6 (crash-safety) recording the direct-write decision and rationale, and to Open question 7 (full-cluster read) confirming the guest reads the whole first cluster. Keep it to a few sentences, matching the phase-1/2 resolution style. Do NOT add this phase file to `order.yml`. |
| 3d | low | sonnet | none | From the worktree root: `pre-commit run --all-files`; `make instar` (confirm `amend.bin` builds, is within 384 KiB, and `core.bin` is unchanged from phase 2 at 99%); `make test-rust` (all suites pass). Then stage and present a single commit for steps 3a–3c with the CLAUDE.md message convention (≤50-char first line ending in a period, 75-char body wrap, `Prompt:` paragraph, `Signed-off-by`, `Co-Authored-By`/`Assisted-By` naming model + 1M context + effort). Do not push. |

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. After each step the management session reads
the actual changed files (does not trust the summary), confirms no
unrelated files changed, runs the named gates, and then commits,
retries with a sharper brief, or upgrades the model. The sandbox
**denies direct `cargo`**; validate via `make instar`,
`make test-rust`, and `pre-commit run --all-files`.

### Model and effort notes

- 3a, 3c, 3d are mechanical wiring/docs/gates; sonnet at low effort
  with the exact rebase template suffices.
- 3b is the guest bridge and uses opus: fixed-address memory
  discipline, the full-cluster read, the cross-check, the
  error-code mapping, and the patch-apply loop must all be exact —
  a wrong offset corrupts images.
- When in doubt, skew to the more capable model.

### Management session review checklist

After each step:

- [ ] Read the changed files — don't trust the summary.
- [ ] No unrelated files modified; the `Commands` enum and host CLI
      are untouched (that is phase 4).
- [ ] `make instar` builds; `amend.bin` is listed, within 384 KiB,
      and `core.bin` is unchanged from phase 2.
- [ ] `make test-rust` passes (planner tests still green).
- [ ] `pre-commit run --all-files` clean.
- [ ] The `_start` sequence matches the mission: config →
      sector-0/format → parse → cross-check → layout guard →
      full-cluster read → planner → apply → result/complete, with
      every error path mapping to the right `AmendResult::ERROR_*`.
- [ ] The header write is direct (no corrupt-bit guard), matching
      resize/rebase.

## Administration and logistics

### Success criteria

Phase 3 is complete when:

* `src/operations/amend/` builds an `amend.bin` guest binary wired
  into `src/build.sh`, `scripts/check-binary-sizes.sh`,
  `src/Cargo.toml` members, and the Makefile `test-rust` excludes.
* The guest reads the config + sector 0 + full first cluster,
  cross-checks against `AmendConfig`, calls `plan_amend_qcow2`,
  applies the patches, and reports via `send_amend_result` +
  `send_complete`, with every error path mapping to the correct
  `AmendResult::ERROR_*` code.
* The header rewrite is a direct write (no corrupt-bit guard),
  matching resize/rebase (master-plan Open question 6 resolved).
* `make instar` builds, `amend.bin` is within 384 KiB, `core.bin`
  is unchanged, `make lint`/`make test-rust`/`pre-commit
  run --all-files` are clean.
* The master plan's Open questions 6 and 7 are updated.

### Future work created by this phase

- Phase 4 (host CLI) adds the `Amend(AmendArgs)` `Commands` variant,
  parses `-o compat=…,lazy_refcounts=…`, probes the image
  host-side to populate `AmendConfig` (including the cross-check
  fields), launches this guest binary, harvests the
  `AmendRunResult` (phase-1 decode arm), and renders human/json.
- If Open question 4 leaves the `create` feature on `crates/amend`,
  revisit dropping it once phase 4 confirms the op binary size.
- The 512-byte-sector torn-header window (Open question 1) is a
  documented, qemu-equivalent residual; a corrupt-bit guard is a
  possible future hardening.

### Documentation index maintenance

This is a phase plan, not a master plan. It is **not** added to
`docs/plans/order.yml`. The master plan links to it from its
Execution table (already present).

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
