# `instar amend` subcommand

## Status: Complete

Phases 1-9.

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on external concepts (QCOW2 header layout, the qcow2 v2/v3
feature model, `qemu-img amend` semantics, lazy refcounts), research
as needed to give a confident answer. Flag any uncertainty
explicitly rather than guessing.

All planning documents go in `docs/plans/`. Phase plans for this
master plan are named `PLAN-amend-phase-NN-<descriptive>.md`
alongside this file and linked from the Execution table below.
They are *not* added to `docs/plans/order.yml` — only the master
plan is.

Consult `ARCHITECTURE.md` for the overall system structure (host
VMM, KVM guest, call table, device emulation). Consult `AGENTS.md`
for build commands, project conventions, code organisation, and the
security-model summary. Consult `docs/qcow2/` for the qcow2 format
notes and `docs/commentary/` for design rationale.

I prefer one commit per logical change, and at minimum one commit
per phase. Each commit should be self-contained: it should build,
pass tests, and have a clear commit message explaining what changed
and why.

## Situation

`PLAN-convert-followups.md` enumerated the `qemu-img` subcommands
deferred from the original convert effort. That roster —
`measure`, `create`, `resize`, `rebase`, `commit`, `map`,
`snapshot` — is now **complete** (`PLAN-snapshot.md` closed it),
and `check --repair` shipped on top (`PLAN-check-repair.md`). The
convert-followups "Future work" section names `qemu-img amend` as
the next candidate sibling capability:

> If `qemu-img amend` ever becomes useful (changing image options
> after creation, e.g. cluster size), add it as a fourth phase.
> — `PLAN-convert-followups.md:144`

`PLAN-check-repair.md:430` likewise lists it as a deferred sibling.
The operations matrix in `docs/usage.md:906` rates `amend` as
**Low priority, oVirt-only**. It is one of a small set of
unimplemented low-priority verbs (`amend`, `dd`, `bitmap`,
`bench`); `amend` is the one with an existing plan slot and the
clearest correctness contract, so it is the natural next pickup.

**What `qemu-img amend` does.** It changes *format options* of an
existing image in place, without converting or recreating it. For
qcow2 the load-bearing options are the **compatibility version**
(`compat=0.10` ⇔ qcow2 v2, `compat=1.1` ⇔ qcow2 v3) and the
**`lazy_refcounts`** flag (a qcow2 v3 *compatible*-feature bit).
qemu-img's amend also nominally accepts `refcount_bits`,
`cluster_size` (rejected — not amendable), `data_file`,
`data_file_raw`, encryption parameters, and `backing_file` /
`backing_fmt`. Of these, only `compat` and `lazy_refcounts` change
header bytes without rewriting cluster/refcount data; the rest are
either out of scope, expensive structural rewrites, or already
owned by another subcommand (`rebase` owns backing-file changes).

**Why this is the right next step and lower-risk than its
predecessors.** `amend`'s in-scope options touch only the qcow2
**header** — the fixed 72-byte (v2) / 104-byte (v3) region plus
the header-extension area. Unlike `resize` (rewrites L1 / refcount
tables) and `snapshot`/`check --repair` (rewrite the snapshot
table and refcount tree), the in-scope amend operations do **not**
touch L1, L2, refcount blocks, or cluster data. This makes amend
the most contained mutation operation in the roster — but it is
*not* trivial, because the v2⇔v3 transition has real subtleties
(header length change, v3-only field initialisation/zeroing,
refcount-width constraints, and possible header-extension
relocation — see Open questions).

The relevant existing infrastructure this plan builds on:

- **In-place mutation idiom.** `resize` and `rebase` already
  establish the host→guest mutation pattern: probe format
  host-side, open the file `O_RDWR`, attach it as the
  virtio-block *output* device, populate a `*Config` struct at
  `OPERATION_CONFIG_ADDR`, launch the guest op, harvest a
  `*Result` message over the serial channel, render human/json.
  `run_resize_guest` (`src/vmm/src/main.rs:4664`) and
  `run_rebase_guest` (`src/vmm/src/main.rs:4986`) are the
  reference flows. `rebase`'s **selective header-field patch**
  (`src/crates/rebase/src/qcow2.rs:265` — a 12-byte write at the
  backing-file offset, no full-header rebuild) is the closest
  prior art for amend, which is also a small header patch.

- **qcow2 header model.** `src/crates/qcow2/src/lib.rs` already
  parses every field amend cares about: `version`,
  `incompatible_features`, `compatible_features`,
  `autoclear_features`, `refcount_bits` (derived from
  `refcount_order`), `header_length`, and the header-extension
  walk. Named feature-bit constants exist: `COMPAT_LAZY_REFCOUNTS
  = 1 << 0`, `INCOMPAT_DIRTY/CORRUPT/EXTERNAL_DATA/COMPRESSION/
  EXTENDED_L2`. `compat_str()` / `compat_value()` already map
  version → "0.10"/"1.1". Field offset constants (`VERSION_OFFSET`,
  `INCOMPATIBLE_FEATURES_OFFSET = 72`, `COMPATIBLE_FEATURES_OFFSET
  = 80`, `AUTOCLEAR_FEATURES_OFFSET = 88`, `REFCOUNT_ORDER_OFFSET
  = 96`, `HEADER_LENGTH_OFFSET = 100`) are all defined.

- **qcow2 header writer.** `build_header()` in
  `src/crates/qcow2/src/create.rs:329` constructs a complete v3
  header into a caller-supplied buffer using the shared
  `write_be_u32/u64` helpers. `resize`'s grow path reuses it
  wholesale (`src/crates/resize/src/qcow2.rs:513`). **Caveat:**
  `build_header()` always emits a *v3* header (writes
  `header_length = 104`, refcount_order, the v3 feature words).
  A `compat=0.10` *downgrade* needs a v2-shaped header (72-byte
  fixed region, no v3 words), which `build_header()` cannot
  currently produce — see Open questions.

- **Planner-crate convention.** `src/crates/{create,resize,rebase,
  commit,snapshot}/` each expose `no_std` planner functions that
  take parsed state + options and emit a patch list, with inline
  `#[cfg(test)]` unit tests and `tests/` round-trip suites. amend
  gets `src/crates/amend/`.

- **Guest-op convention.** `src/operations/{resize,rebase,…}/`
  each build a `no_std`, `panic = "abort"`, `opt-level = "z"`
  guest binary with a `#[no_mangle] _start()` that reads its
  `*Config`, reads sector 0 via `read_output_sector`, dispatches
  by format, applies patches, and calls
  `call_table.send_*_result`. amend gets `src/operations/amend/`,
  checked under the 384 KiB cap by
  `scripts/check-binary-sizes.sh:65`.

- **Call-table / ABI boundary.** `src/shared/src/lib.rs` holds the
  `repr(C)` `*Config`/`*Result` structs near
  `OPERATION_CONFIG_ADDR` (`ResizeConfig` line 3075,
  `RebaseConfig` line 3259, etc.) and the `CallTable` function
  pointers. `crates/guest-protocol/proto/guest.proto` holds the
  `GuestMessage` oneof (resize_result = 12, rebase_result = 13,
  …). amend adds an `AmendConfig`, `AmendResult`,
  `send_amend_result` pointer, and `AmendResultMessage`.

- **`-o` option parsing.** `parse_o_options` /
  `parse_create_o_options` (`src/vmm/src/main.rs:8776` / `:9034`)
  split `-o k=v,k=v`, validate `(target, key)` against a
  whitelist, and parse typed values (`parse_o_bool`,
  `parse_o_size_u32`). amend's surface is **entirely** `-o`-driven
  (`qemu-img amend -o compat=…,lazy_refcounts=…`), so this helper
  is central.

- **Test / baseline / fuzz harnesses.** Python integration tests
  in `tests/test_*.py` (cross-checked against `qemu-img` as
  oracle, manifest-driven images, profile-grouped baselines via
  `tests/base.py`); cross-version baselines generated by
  `instar-testdata/scripts/generate-baselines.py` into
  `expected-outputs/<op>-info-json/<target>/<version>/`;
  coverage-guided fuzz targets in `src/fuzz/fuzz_targets/`;
  differential fuzz `op_*` functions in
  `scripts/differential-fuzz.py`. Note the qcow2 unit-test
  feature-gate quirk: `cargo test -p qcow2 --features create`
  (see `Makefile:484-511`).

## Mission and problem statement

Implement `instar amend` such that:

1. It accepts a subset of `qemu-img amend`'s surface:
   ```
   instar amend [-f FMT] -o OPTIONS [-q] [--output {human,json}] FILENAME
   ```
   - `-o OPTIONS` is the only way to specify changes, matching
     qemu-img. v1 recognises exactly two qcow2 keys:
     - `compat=0.10|1.1` — set the qcow2 compatibility version.
     - `lazy_refcounts=on|off` — set/clear the v3 lazy-refcounts
       compatible-feature bit.
     Any other recognised-but-unsupported qemu-img amend key
     (`refcount_bits`, `data_file`, `data_file_raw`, encryption
     keys, `backing_file`/`backing_fmt`) is **rejected at runtime
     with a clear "not yet supported" error** — the same posture
     `create`/`resize` took for `--object`/`--image-opts`.
     `cluster_size` is rejected as not-amendable (qemu-img rejects
     it too).
   - `-f FMT` forces format detection.
   - `-q` suppresses the success line; errors still go to stderr.
   - `--output {human,json}` selects rendering.

2. It is **qcow2-only** in v1. Non-qcow2 inputs (raw, vmdk, vhd,
   vhdx) are rejected with a clear error — qemu-img amend itself
   only meaningfully supports qcow2 (and luks, which is out of
   scope). This mirrors `snapshot` (qcow2-only) and `check
   --repair` (qcow2-only).

3. The in-scope mutations are header-only and must be applied
   safely in place:
   - **`compat=1.1` upgrade (v2 → v3).** Set `version = 3`,
     initialise the v3 feature words (incompatible/compatible/
     autoclear = 0 unless `lazy_refcounts` is also requested),
     set `refcount_order` to match the existing refcount width,
     set `header_length = 104`, and preserve / relocate any
     header extensions that currently sit at the v2 offset 72
     into the post-v3-header region (offset ≥ 104). No L1 /
     refcount / cluster changes.
   - **`compat=0.10` downgrade (v3 → v2).** Refuse if any
     incompatible feature is set (`DIRTY`, `CORRUPT`,
     `EXTERNAL_DATA`, `COMPRESSION`, `EXTENDED_L2`) or if the
     image uses any v3-only structure a v2 reader cannot
     understand (non-16-bit refcounts, zero clusters via extended
     L2, compression). On success, write `version = 2`, zero the
     v3-only fixed-header words, and re-lay the header to the v2
     72-byte shape (relocating extensions back to offset 72). If
     `lazy_refcounts` was set, it must be cleared as part of the
     downgrade (lazy refcounts is a v3 feature).
   - **`lazy_refcounts=on|off`** (v3 only; requesting it while
     downgrading to v2 or against a v2 image is an error): set or
     clear `COMPAT_LAZY_REFCOUNTS` in `compatible_features`. When
     clearing, the at-rest image is already refcount-consistent
     (instar never opens images read-write outside an operation),
     so no refcount flush is required — but this assumption must
     be validated against the dirty bit and documented.

4. It cross-validates against `qemu-img amend`: after an instar
   amend, `qemu-img info`/`check`/`compare` on the result must
   agree with the same image amended by `qemu-img amend`
   (info-equivalence baselines, same model as create/resize).

5. It is exercised by Rust unit tests, Rust round-trip tests,
   Python integration tests, cross-version baselines,
   coverage-guided fuzzing of the planner, and differential
   fuzzing against qemu-img.

**Out of scope for v1** (see Future work): `refcount_bits` changes
(require rewriting the entire refcount tree); `data_file` /
`data_file_raw` external-data-file attach/detach; encryption /
LUKS amend; `backing_file` / `backing_fmt` via amend (owned by
`rebase`); any non-qcow2 format; converting between cluster sizes.

## Open questions

These need resolution during detailed (phase) planning. Each is a
real fork in the design, grounded in what the code does today.

1. **Downgrade header writer.** `build_header()`
   (`create.rs:329`) only emits v3 headers. A `compat=0.10`
   downgrade needs a v2-shaped header (72-byte fixed region, v3
   words absent/zeroed). Options: (a) extend `build_header()` with
   a target-version parameter; (b) write a small amend-specific
   header serializer in `src/crates/amend/`; (c) do a selective
   field patch (zero offsets 72–103, set version, fix
   header_length) à la rebase. Leaning toward (c) for the
   downgrade and reusing `build_header()` for the upgrade, but the
   header-extension relocation question (below) may force a fuller
   rebuild.

2. **Header-extension relocation.** Header extensions live
   *after* the fixed header: at offset 72 for v2, at offset 104
   (`header_length`) for v3. A v2→v3 upgrade must move existing
   extensions from 72 to ≥104 (and a v3→v2 downgrade must move
   them back), being careful not to collide with the first
   refcount/L1 cluster. Question: do any of our test images / real
   oVirt images actually carry v2 header extensions, or is the
   common case "no extensions, so relocation is a no-op"? If
   relocation is needed, where does the extra space come from —
   is there always slack to `cluster_size`? `qemu-img` rewrites
   the header cluster; we likely must too. **Research qemu's
   `qcow2_amend_options` / `qcow2_update_header` for the exact
   relocation algorithm and the corrupt-bit crash-safety
   ordering.**

3. **Refcount-width constraint on downgrade.** qcow2 v2 only
   supports 16-bit refcounts. If a v3 image has
   `refcount_bits != 16`, a `compat=0.10` downgrade is impossible
   without rewriting the refcount tree (out of scope). Confirm we
   simply *refuse* such downgrades with a clear error rather than
   attempt them, matching the "refuse rather than guess" posture
   from `check --repair`.

4. **Is amend ever a no-op / idempotent?** If the requested
   options already match the current header (e.g.
   `compat=1.1` on an already-v3 image with the same lazy flag),
   does qemu-img rewrite anyway or short-circuit? Decide whether
   instar emits an `ACTION_NOOP`-style result (cf. resize's noop
   action) and whether the file mtime/header should be touched at
   all. Match qemu-img's observable behaviour where it is stable
   across versions; document divergences in
   `KNOWN_AMEND_DIVERGENCES`.

5. **Lazy-refcounts clear safety.** qemu, when clearing
   `lazy_refcounts`, ensures refcounts are flushed/consistent
   before dropping the bit. instar only ever touches images via a
   single guest operation and never leaves an image open
   read-write, so an at-rest image should already be consistent.
   Validate this claim: if the `DIRTY` incompatible bit is set,
   refuse (the image was left dirty by some other writer);
   otherwise clearing the compatible bit is safe. Confirm against
   the qcow2 spec and qemu source.

   **Resolved in phase 2** (`PLAN-amend-phase-02-qcow2-planner.md`,
   `src/crates/amend/`):
   - **OQ1 / OQ2 (downgrade writer + relocation):** option (A) — a
     dedicated copy-and-adjust serializer in the amend crate (not
     `build_header`, which only emits v3 and cannot preserve
     existing extensions). A version change rebuilds the whole
     header cluster, relocating the extension area + backing-file
     string to the new fixed-header boundary (72↔104) and bumping
     `backing_file_offset` by the shift; `ExtensionRelocationUnsupported`
     only when the shift would overflow the cluster. Fixture work
     confirmed real qemu layouts (v2 exts at 72; v3 `header_length`
     = 112 with a feature-name-table ext; backing-file always
     preceded by the backing-format ext). Upgrade writes the
     minimal `header_length = 104` and omits the feature-name-table
     ext (info-equivalent; any residual divergence is recorded in
     phase 6).
   - **OQ3 (refcount width):** downgrade refuses `refcount_bits != 16`
     with `DowngradeRefcountWidth`.
   - **OQ4 (no-op):** target == current for both version and lazy
     ⇒ `AmendAction::NoOp`, zero patches (idempotent; the file is
     not touched). Whether qemu rewrites anyway on a no-op is
     reconciled in phase 6.
   - **OQ5 (lazy clear safety):** any `DIRTY`/`CORRUPT` image is
     refused (`Dirty`), so a non-dirty lazy clear is safe with no
     refcount flush. A v3→v2 downgrade silently clears inherited
     lazy; only an *explicit* `lazy_refcounts=on` against a v2
     target is refused (`LazyRequiresV3`). (A review caught and
     fixed a first-cut bug that rejected the silent-clear case.)
   - **OQ6 (crash-safety) remains for phase 3** — the planner emits
     only the final target bytes; write ordering / corrupt-bit
     guarding is a guest concern.

6. **Crash-safety / write ordering.** Header rewrites must be
   crash-safe. `check --repair` established the pattern of
   guarding risky in-place mutation with the `corrupt`
   incompatible bit and ordering writes so a crash leaves either
   the old or new valid state. Does a single-cluster header
   rewrite need that machinery, or is a single sector/cluster
   write atomic enough at our guarantees? Decide and document.

   **Resolved in phase 3** (`PLAN-amend-phase-03-guest.md`,
   `src/operations/amend/`): **a direct header write, no corrupt-bit
   guard**, matching `resize`/`rebase` (and qemu's own
   `qcow2_update_header`, which rewrites the header cluster without
   the guard). The load-bearing fixed-header fields live in the
   first sector (atomic flip), and the host fsyncs the output file
   after the guest halts (`vmm/src/main.rs` `file.sync_all()`); the
   corrupt-bit dance is reserved for `check --repair`'s
   multi-cluster, multi-phase mutation. Residual: a 512-byte-sector
   image whose relocated extensions span multiple sectors has a
   torn-header window on crash — the same window qemu has — accepted
   for v1, with a corrupt-bit guard noted as possible future
   hardening.

7. **Does amend need to read more than sector 0?** resize/rebase
   read sector 0 to detect format, then read more as needed. amend
   needs the full header cluster (up to `cluster_size`, ≥ 512 B)
   to see header extensions and compute relocation. Confirm the
   guest reads the whole first cluster, not just sector 0, before
   planning.

   **Resolved in phase 3:** yes — the guest reads the whole first
   cluster (`read_byte_range(.., 0, .., cluster_size)`) into a
   dedicated buffer before planning, after a `cluster_size <=`
   scratch-limit bounds check. A defensive layout guard also
   refuses images whose refcount-table or L1 offset falls inside
   cluster 0 (which the whole-cluster rewrite would clobber).

8. **ABI footprint.** `AmendConfig`/`AmendResult` shapes: how much
   do we pass? Likely `target_format`, `flags` (a bitfield:
   set-compat-v2/v3, set-lazy-on/off, quiet), the parsed current
   header summary the host already probed (version, refcount_bits,
   feature words, cluster_size, header_length), and an error/action
   code back. Decide whether the host pre-parses the header and
   passes a summary (like resize passes `current_virtual_size`) or
   the guest re-parses from the device. Prefer the resize model:
   host probes + passes a cross-check, guest re-parses and
   validates against it.

   **Resolved in phase 1.** The ABI froze on the resize model:
   `AmendConfig` (128 B) carries `target_format`, `flags`
   (present-bit + value-bit pairs: `FLAG_SET_COMPAT`/
   `FLAG_COMPAT_V3`, `FLAG_SET_LAZY`/`FLAG_LAZY_ON`, `FLAG_QUIET`),
   and the host-probed cross-check (`current_version`,
   `current_refcount_bits`, both feature words, `cluster_size`,
   `virtual_size`); `AmendResult` (64 B) returns `action`
   (noop/amended), `resulting_version`, `resulting_lazy_refcounts`,
   and `error`. Only one call-table pointer was added
   (`send_amend_result`), bumping `CallTable::VERSION` 17 → 18; no
   new device-I/O primitive was needed (resolves Open question 6/7's
   ABI half — amend reuses resize's `read_output_sector` /
   `write_output_sector`). Header-length is *not* carried in the
   config: `cluster_size` bounds the header-cluster read and the
   guest derives the rest from its own re-parse.

## Execution

Phase plans are written one at a time, at the recommended effort,
and reviewed before the next is drafted. The phase breakdown
mirrors the established subcommand-plan shape (ABI → planner →
guest → host → tests → baselines → fuzz → docs), compressed
because the mutation is header-only.

| Phase | Plan | Status |
|-------|------|--------|
| 1. ABI: `AmendConfig`/`AmendResult`, call-table `send_amend_result`, `AmendResultMessage` proto, magic/flag/error constants | PLAN-amend-phase-01-abi.md | Complete |
| 2. qcow2 amend planner crate (`src/crates/amend/`): header patch computation for compat up/down + lazy toggle, all validation (downgrade blockers, refcount-width, extension relocation), inline unit tests | PLAN-amend-phase-02-qcow2-planner.md | Complete |
| 3. Guest op (`src/operations/amend/`): read config, read full header cluster, dispatch qcow2, apply patches, send result; binary-size check | PLAN-amend-phase-03-guest.md | Complete |
| 4. Host VMM subcommand: `AmendArgs` clap surface, `-o` option parsing + validation, host-side format probe, `run_amend`/`run_amend_guest`, human/json rendering | PLAN-amend-phase-04-host-cli.md | Complete |
| 5. Rust round-trip tests (`src/crates/amend/tests/`): amend → re-parse, assert header invariants for each transition | PLAN-amend-phase-05-rust-tests.md | Complete |
| 6. Python integration tests (`tests/test_amend.py`): cross-check vs `qemu-img amend` with post-op `info`/`check`/`compare`, known-divergence registry | PLAN-amend-phase-06-integration.md | Complete |
| 7. Cross-version baselines: `AMEND_CASES` in `generate-baselines.py`, `expected-outputs/amend-info-json/`, testdata push | PLAN-amend-phase-07-baselines.md | Complete (testdata push operator-gated) |
| 8. Coverage fuzz (`fuzz_amend_planners.rs`) + differential fuzz (`op_amend` in `differential-fuzz.py`) | PLAN-amend-phase-08-fuzz.md | Complete — harnesses landed; the cluster-size defect they found (core `.bss` overflow into the op region) is root-caused and fixed (see Defects) |
| 9. Docs: `docs/amend.md`, `docs/usage.md`, `CHANGELOG.md`, `ARCHITECTURE.md`/`README.md`/`AGENTS.md`, `index.md`/`order.yml` | PLAN-amend-phase-09-docs.md | Complete |

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The management session is reserved for
planning, review, and decision-making. The workflow per step:
**plan** (high effort) → **spawn a sub-agent** with the brief →
**review the actual files** (the summary describes intent, not
necessarily what changed) → **fix or retry** (improve the brief or
upgrade the model) → **commit** once satisfied. Use
`isolation: "worktree"` for risky/experimental steps.

### Planning effort

The master plan is created at **high effort**. Phase 2 (qcow2
planner — header relocation, downgrade safety, refcount-width
constraints) and phase 1 (ABI) and phase 8 (differential fuzz
oracle) should be planned at **high effort**; they involve
format-spec interpretation, the v2/v3 transition algorithm, and
cross-validation correctness. Phases 3–7 and 9 follow
well-established patterns from resize/rebase and can be planned at
**medium effort** once the briefs front-load the research.

### Step-level guidance

Each phase plan includes a step table:

```
| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a   | medium | sonnet | none     | One-sentence summary of what to do and which files to touch |
| 1b   | high   | opus   | worktree | Why this needs high effort: requires understanding X to do Y |
```

**Effort levels** — high: multiple files, judgment calls,
non-obvious invariants, external spec research. medium: clear
brief, well-defined approach. low: purely mechanical.

**Model choice** — opus for deep reasoning / cross-file
architectural understanding / subtle correctness (the v2⇔v3
transition, header relocation, refcount safety, ABI changes
bridging VMM and guest). sonnet for well-briefed implementation.
haiku for mechanical tasks. **When in doubt, skew to the more
capable model** — a failed implementation wastes more than a
heavier model costs. A detailed brief compensates for a lighter
model.

**Brief for sub-agent** — write it as if briefing a colleague who
has never seen the codebase: what to change, which files, what
patterns to follow, and non-obvious constraints (the 384 KiB guest
binary cap, the `no_std` requirement of the format/planner crates,
the call-table boundary, the `repr(C)` layout discipline, the
qcow2 `--features create` test gate). Front-load the research the
planner already did. For example, instead of "write the qcow2
upgrade patch", write "in `src/crates/amend/src/qcow2.rs`,
implement `plan_amend_qcow2(header: &QcowHeader, req: &AmendRequest,
scratch: &mut [u8]) -> Result<AmendPlan, AmendError>`. For the
v2→v3 upgrade, reuse the field offsets from
`src/crates/qcow2/src/lib.rs` (`VERSION_OFFSET`,
`REFCOUNT_ORDER_OFFSET = 96`, `HEADER_LENGTH_OFFSET = 100`) and
emit a single header-cluster Write; relocate any extension at
offset 72 to offset 104 first."

### Management session review checklist

After a sub-agent completes, verify:

- [ ] The files that were supposed to change actually changed
      (read them, don't trust the summary).
- [ ] No unrelated files were modified.
- [ ] `make instar` builds and `make lint` is clean.
- [ ] Guest binaries pass `make check-binary-sizes` (384 KiB).
- [ ] `make test-rust` (including `cargo test -p qcow2
      --features create` and the new `amend` crate) passes.
- [ ] Relevant `make test-integration` targets pass.
- [ ] `pre-commit run --all-files` passes.
- [ ] The changes match the intent of the brief — semantically,
      not just syntactically.
- [ ] Commit message follows project conventions (Co-Authored-By
      with model, context window, effort level, and other
      settings; `Signed-off-by`; `Prompt:` paragraph).

## Administration and logistics

### Success criteria

We will know this plan has been successfully implemented because
the following statements will be true:

* `instar amend -o compat=1.1 <v2-image>` produces an image that
  `qemu-img info` reports as `compat 1.1`, and that
  `qemu-img check` and `qemu-img compare` against the original
  (data-equivalent) pass.
* `instar amend -o compat=0.10 <v3-image-without-v3-features>`
  produces a `compat 0.10` image; the same amend against an image
  with any blocking v3 feature is refused with a clear,
  qemu-comparable error.
* `instar amend -o lazy_refcounts=on|off <v3-image>` toggles the
  compatible-feature bit and nothing else.
* For every in-scope case, an `instar`-amended image is
  info-equivalent to the same image amended by `qemu-img amend`
  across the supported qemu-img version matrix (baselines).
* `make instar` builds and `make lint` is clean.
* Guest binaries pass `make check-binary-sizes` (384 KiB limit);
  the new `amend.bin` is registered in
  `scripts/check-binary-sizes.sh`.
* All Rust unit tests pass (`make test-rust`), including the new
  `amend` crate and round-trip tests.
* All Python integration tests pass (`make test-integration`),
  including `tests/test_amend.py`.
* Coverage-guided fuzzing of the amend planner and differential
  fuzzing against `qemu-img amend` run clean.
* `pre-commit run --all-files` passes.
* Documentation in `docs/` (`amend.md`, `usage.md`) and
  `ARCHITECTURE.md`, `README.md`, `AGENTS.md`, `CHANGELOG.md`,
  `docs/plans/index.md`, `docs/plans/order.yml` are updated.

### Future work

Obvious extensions deferred from v1:

* **`core.bin` is at its 64 KiB budget ceiling (surfaced in phase
  1).** Wiring amend's result sender into `core` took it from
  63 680 B (97 %) to 65 304 B (99 %) — and only after moving the
  result strings off the wire to keep it under the 65 536 B cap
  (see phase-1 plan Open question 5). ~232 B of headroom remain.
  The next subcommand/phase that adds anything to `core` will
  overflow it; at that point we must either keep trimming
  per-feature or lift the core memory budget (move the operations
  load address in `src/shared/src/lib.rs`'s memory map — a
  loader/layout change affecting every guest binary). An operator
  decision, not resolved here.
* **`refcount_bits` amend.** Changing the refcount width requires
  rewriting the entire refcount tree (and possibly growing/
  shrinking refcount blocks). Substantial; reuse the
  `src/crates/snapshot/` refcount mutators and the resize
  refcount-table growth helpers if a real workflow demands it.
* **External data file amend** (`data_file`, `data_file_raw`):
  attach/detach an external data file, manage the
  `INCOMPAT_EXTERNAL_DATA` bit and the `DATA` header extension.
* **LUKS / encryption amend** (changing keyslots/parameters).
* **`backing_file` / `backing_fmt` via amend.** qemu-img amend can
  change these, but instar already owns this via `rebase -u`;
  decide whether to alias or leave it to `rebase`.
* **Non-qcow2 amend.** qemu-img amend is qcow2-centric; revisit
  only if a concrete need appears.
* **Header-extension relocation hardening.** If v1 punts on images
  carrying v2 header extensions (refusing rather than relocating),
  lift that restriction here.
* **Compressed-image downgrade (documented divergence, phase 6).**
  `instar amend -o compat=0.10` refuses a v3 image with the
  compression (zstd) incompatible feature
  (`ERROR_DOWNGRADE_BLOCKED_FEATURE`), because v2 cannot represent
  `compression_type` and instar's v1 does not recompress cluster
  data — the "refuse rather than guess" posture. qemu-img 10.0.8
  *accepts* the same downgrade (rewriting the compression type).
  `tests/test_amend.py` asserts instar's refusal and records the
  divergence without failing. Lifting this would mean recompressing
  zstd→zlib (out of v1 scope).

### Bugs fixed during this work

List any bugs encountered and fixed during development here. At the
start of phase 1, scan the `security-audit` GitHub issue tracker
for any open qcow2-header / feature-bit / version-detection issues
that this work should resolve or be aware of.

### Defects found during this work

- **Cluster-size `ERROR_HEADER_MISMATCH` (FIXED).** `instar amend`
  spuriously failed with `ERROR_HEADER_MISMATCH` for certain qcow2
  cluster sizes (512, 2048, 16384, 32768, 262144 …) while `qemu-img
  amend` accepted the same operation; the default 64 KiB cluster and
  many other sizes succeeded, which is why the by-example tests (phases
  5–7, all built at the default cluster) never caught it. Found by the
  phase-8 differential fuzzer (`op_amend`).

  **Root cause:** core.bin's `.bss` overflowed its 64 KiB budget
  (`0x10000`–`0x20000`) into the operation region at `0x20000`. The
  static `OUTPUT_DEVICE: Option<VirtioBlock>` was linked at `0x20380`,
  and when core initialised the output block device it wrote the
  `VirtioBlock` struct there — clobbering ~72 bytes of the loaded op's
  code at `0x20380`–`0x203c7`. For amend that region held the header
  cross-check, whose corrupted bytes then branched data-dependently on
  the cluster-size values (error for some sizes, accidental success for
  others). Every op's bytes there were corrupted, but only amend had
  critical branch logic at that offset. The flat-binary size lint missed
  it because the `.bin` excludes `.bss` (the file was under budget while
  the runtime extent reached `0x203d0`).

  Root-caused with host-only KVM debugging (a hardware data-write
  watchpoint on `0x20380` trapped the writing instruction in core.bin) —
  the guest op is too codegen-fragile for source instrumentation.

  **Fix:** `OPERATION_LOAD_ADDR` raised `0x20000` → `0x22000` (giving
  core a 72 KiB region so its `.bss` no longer overlaps the op);
  `src/operations/*/linker.ld` `OPERATION_BASE` updated to match; and
  `scripts/check-binary-sizes.sh` rewritten to validate the
  `.bss`-inclusive ELF memory extent (not just the flat `.bin` size), so
  this class of overflow is caught in future. Verified: amend passes all
  13 cluster sizes (512 … 2 MiB), the differential fuzzer reports 0
  divergences over 60 iterations, and the resize/create/check/snapshot/
  map suites are unregressed.

- **resize grow on a 64 KiB-cluster qcow2 (PRE-EXISTING, separate, out
  of scope).** `instar resize <img> 8M` on a `qemu-img create -f qcow2
  -o cluster_size=65536 <img> 4M` image fails with resize error 13
  (header mismatch). It reproduces identically on the clean branch
  before this fix (so it is **not** caused by the `OPERATION_LOAD_ADDR`
  move) and is not in the resize test suite (which passes). Flagged for
  a separate investigation; likely a distinct resize-planner accounting
  issue, unrelated to the amend `.bss` corruption above.

### Documentation index maintenance

This master plan has been added to:

* **`docs/plans/index.md`** — a row in the *Master plans* table
  (dated 2026-06-15, status *Complete (phases 1-9)*, phases listed).
* **`docs/plans/order.yml`** — an entry so it appears in the docs
  navigation. Phase files are *not* added to `order.yml`.

All phases are complete; the `index.md` status column has been
updated to *Complete*.

### Back brief

Before executing any step of this plan, the executing agent should
back brief the operator as to its understanding of the plan and how
the intended work aligns with it.
