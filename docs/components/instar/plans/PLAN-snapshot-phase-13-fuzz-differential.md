# PLAN-snapshot phase 13: differential fuzzing extension

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns
(`scripts/differential-fuzz.py` — the `op_*` function contract
(return `None` or a divergence dict), the `_*_option_picker`
pattern, `run_iteration`'s dispatch, `run_instar` / `run_qemu_img`
/ `_run_qemu_io`, `compare_exit_codes`;
`.github/workflows/differential-fuzz.yml` (nightly 1000
iterations, PR lane 100 on fuzzer-script changes, post-merge 200);
the phase 6–8 shell harnesses `tools/snapshot-*-matrix.sh` for the
byte-identity methodology this phase ports to Python; and
`docs/quirks.md`'s snapshot sections, which are the divergence
catalogue the chain generator must respect). Ground every
comparison rule in the quirks catalogue and the probe transcripts
below — do not invent normalizations the probes did not need.

Phase plans for the parent master plan live alongside it in
`docs/plans/` and are named
`PLAN-snapshot-phase-NN-<descriptive>.md`. The master plan is
[PLAN-snapshot.md](/components/instar/plans/PLAN-snapshot/). This is phase 13 of
fourteen.

I prefer one commit per logical change, and at minimum one commit
per phase. Each commit should be self-contained: it should build,
pass tests, and have a clear commit message explaining what
changed and why.

## Situation

Phases 1–12 delivered the snapshot subcommand, harnesses,
baselines, the integration suite, and the coverage-guided fuzz
targets. Phase 13 adds the master plan's last verification layer:
a `snapshot` operation in the differential fuzzer that applies a
random create/delete/apply chain to identical images via instar
and qemu-img and demands byte-identical results.

### Established infrastructure facts

1. **The differential fuzzer is op-modular.** Each operation is
   an `op_<name>(instar_bin, instar_copy, qemu_copy, fmt,
   timeout, rng)` function returning `None` (agreement) or a
   divergence dict; `run_iteration` picks 2–4 ops per iteration
   from `OPERATIONS` and dispatches through an `elif` chain.
   Mutating ops (`resize`, `rebase`, `commit`) ignore the
   iteration's random outer image and build their own via a
   `_<name>_option_picker(rng)`; `op_snapshot` follows that
   precedent exactly.
2. **CI needs no changes.** The nightly lane runs whatever
   `OPERATIONS` contains; the PR lane triggers on
   `scripts/differential-fuzz.py` changes, so this phase's PR
   self-tests with 100 iterations. The fuzz run executes inside
   the `instar-build` container (`/dev/kvm` passed through);
   `qemu-img` / `qemu-io` come from the container's
   `qemu-utils`.
3. **Reproducibility machinery exists**: per-iteration derived
   seeds, `--seed`, divergence reports, optional GitHub issue
   filing. The chain belongs in the divergence dict so a report
   reproduces without the log.
4. **The host qemu-img is 10.0.8**, matching the version whose
   behaviour phases 5–8 pinned. The container's version must be
   probed during implementation (step 13b) — `qemu-img
   --version` inside `instar-build` — and the two version gates
   below set accordingly.

### Empirically established behaviour (probes, qemu-img 10.0.8)

All probes run against the phase-12-tip instar binary; transcripts
condensed. Probe scripts used a Python snapshot-table walker
identical in shape to `walk_qcow2_snapshot_table` in
`scripts/extract-fuzz-corpus.py` (phase 12).

1. **Single create is byte-identical after date normalization.**
   `instar snapshot -c snap1 A` vs `qemu-img snapshot -c snap1
   --image-opts driver=qcow2,file.filename=B,file.discard=ignore`
   on identical 16M/64k-cluster images with data: after zeroing
   `date_sec`/`date_nsec` in both live snapshot tables, `cmp -n
   <len(B)>` is identical and instar's longer file tail (64 KiB
   sector rounding) is all zeros. `qemu-img check` clean.
2. **End-of-chain date normalization is NOT sufficient.** A
   c/write/c/a/d chain left a divergence at a *freed* (stale)
   snapshot-table cluster: both tools leave the old table's bytes
   in place when the table is reallocated, and those bytes embed
   each tool's own creation timestamps. The fix is **per-step**
   normalization: patch the live table's dates immediately after
   each successful create, on both sides; all later residue then
   inherits normalized bytes. With per-step normalization the
   full chain is byte-identical (prefix + zero tail), `qemu-img
   check` clean, `qemu-img compare` identical.
3. **Normalize dates to a fixed NONZERO value, not zero.** With
   `date_sec == 0`, `instar snapshot -l` prints a blank DATE
   column while `qemu-img snapshot -l` renders the epoch in
   local time — a degenerate-input renderer divergence (new
   finding; step 13c documents it in `docs/quirks.md`). With
   `date_sec = 0x60000000, date_nsec = 0` both tools print
   `2021-01-14 19:25:36` (host-local) and `-l` outputs are
   byte-identical.
4. **Not-found delete/apply: exit codes match (1/1), stderr text
   differs** (instar explains the matcher semantics; qemu-img
   says "snapshot not found" / "Failed to load snapshot").
   Compare exit codes only, never stderr text. Failed ops leave
   both images unchanged and byte-identical — failure ops are
   safe (and valuable) chain elements.
5. **The comparator catches single-bit divergence.** The very
   first probe run flagged one stray bit in an L1 entry — a
   stale instar binary built during phase 12's deliberate
   can-fire corruption window (source was clean; the artifact
   was not). Rebuilt clean, the probe passed. Treat this as
   proof the oracle works, and as a warning: **rebuild `make
   instar` from verified-clean source before the soak**.

### The divergence catalogue the chain generator must respect

Each documented instar↔qemu divergence (see `docs/quirks.md`,
snapshot sections) must be *avoided* by generation, not absorbed
by weakening the comparator. The op carries a comment table
mapping each rule to its quirks entry:

| Avoided input | Behaviour difference |
|---|---|
| `-c ''` (empty name) | qemu accepts; instar refuses |
| `-c` name > 255 bytes | qemu silently truncates; instar refuses |
| 17th live snapshot | qemu allows; instar `ERROR_SNAPSHOT_TABLE_FULL` (16-snapshot v1 cap) |
| resize within a chain | qemu's later `-a` truncates; instar refuses (`ERROR_L1_SIZE_MISMATCH`); also `instar resize` on snapshot-bearing images is open future work |
| dirty / compressed / encrypted / external-data / bitmap images | instar mutating modes refuse; plain `qemu-img create` bases never produce these, so avoidance is structural |
| `refcount_bits != 16` | instar mutating modes refuse; the qemu-img default is 16, never override `refcount_order` |

Divergences *handled by the comparator* (not avoided):
freed-cluster discard (qemu side runs `file.discard=ignore`),
date stamps (per-step normalization), the sector-granular file
tail (zero-tail tolerance), stderr wording (exit codes only).

## Mission and problem statement

After phase 13 lands: `OPERATIONS` includes `'snapshot'`;
`python3 scripts/differential-fuzz.py --instar … --iterations N
--ops snapshot` runs N snapshot-chain iterations with zero
divergences; the chain is fully reproducible from the divergence
report; the nightly lane exercises snapshot chains with no
workflow edits; and the master plan's differential-fuzzer success
criterion is satisfied.

## Design

### `_snapshot_chain_picker(rng)`

Returns `(base_opts, chain)`:

- **Base image**: qcow2 only. `cluster_size` ∈ {512, 4096,
  65536}; `compat` ∈ {1.1, 0.10}; `extended_l2=on` (only with
  64k clusters and compat 1.1); size ∈ {4M, 16M, 64M}; an
  optional backing file (qcow2 base + overlay — exercises master
  plan point 6; both copies reference the same backing path).
  These mirror the phase 6–8 matrix dimensions
  (`tools/snapshot-*-matrix.sh`).
- **Chain**: 1–8 elements drawn from: `create NAME` (only while
  live count < 16), `delete ARG`, `apply ARG`, `write OFF LEN
  PATTERN` (identical `qemu-io` writes to both copies, making
  later applies content-meaningful). Delete/apply args are
  biased toward existing names/IDs but include ID-like names
  (a snapshot *named* "2" — the `-d`-name-only vs `-a`-ID-first
  asymmetry is prime differential territory), duplicate names
  (first-match semantics), bogus names (failure-op parity), and
  IDs of existing snapshots (valid for `-a`, not-found for
  `-d` under qemu ≥ 4.0 name-only delete semantics). The name
  pool includes a 255-byte name, names with spaces, and UTF-8
  multibyte names; never empty, never > 255 bytes.

### `op_snapshot(...)`

Per the resize/commit precedent, ignores the outer image and:

1. Builds the base (and optional backing) once, copies to
   `snap-instar.qcow2` / `snap-qemu.qcow2`.
2. For each chain element: runs instar vs `qemu-img snapshot`
   (qemu side always
   `--image-opts driver=qcow2,file.filename=…,file.discard=ignore`);
   `compare_exit_codes`; on rc mismatch return a divergence dict
   carrying the full chain + element index. After a successful
   `create`, patch both live tables' `date_sec`/`date_nsec` to
   `0x60000000`/`0` (a `_snapshot_normalize_dates(path)` helper
   reusing the corpus-script walker shape). After every element,
   byte-compare (common prefix + longer-file zero tail) so the
   *earliest* diverging element is reported.
3. At chain end: `qemu-img check` clean on both; `qemu-img
   compare` content identity; `-l` stdout equality —
   `instar snapshot -l` vs `qemu-img snapshot -l` **on the same
   (instar) image** — gated on container qemu ≥ 9.0 (the 8.x→9.0
   list-format change; see `docs/quirks.md`).

### `--ops` CLI filter

Add `--ops op1,op2` to restrict `OPERATIONS` for a run (default:
all). Needed for the snapshot-only soak; generally useful for
focused local debugging of any op. Validate names against
`OPERATIONS` at startup.

## Open questions

### 1. Byte-compare or structural compare?

**Byte-compare** (probes 1–3). It is the strongest available
oracle, the phase 6–8 harnesses already established
bit-for-bit identity under `discard=ignore` across the full
matrix, and probe 5 shows it catches single-bit corruption. The
check/compare/`-l` trio at chain end is a secondary net, mostly
valuable for diagnosing *what* a byte divergence means.

### 2. Should chains deliberately probe the documented divergences?

No. The fuzzer's contract is "any divergence is a bug"; feeding
it expected divergences would mean per-case allow-listing that
rots. The documented divergences are already covered
deterministically: refusal tests in `tests/test_snapshot.py` and
the `tools/snapshot-*-refusals.sh` harnesses. The picker avoids
them and the comment table maps each avoidance to its quirks
entry.

### 3. What about the container's qemu-img version?

Two gates, set after the 13b probe: (a) `-l` stdout comparison
requires qemu ≥ 9.0; (b) the whole `op_snapshot` requires qemu ≥
4.0 (name-only delete). Implement both as a startup version
probe (parse `qemu-img --version`, cache module-level), not
build-time constants — contributors run older distros. If the
container turns out to ship < 9.0, the `-l` check silently skips
(log once) and byte-identity still carries the oracle.

### 4. Does the per-step write of normalized dates perturb anything?

No. Dates are pure metadata: not referenced by any checksum
(qcow2 has none), not read by any mutating path, only rendered
by `-l`/info. Writing identical bytes to both sides preserves
the byte-identity invariant by construction; probe 2/3 chains
passed end-to-end with per-step writes interleaved. When a later
create reallocates the table, instar copies the old bytes
verbatim (`build_snapshot_table`) and qemu re-serializes from
values parsed at open time — both reproduce the normalized dates.

### 5. Iteration cost budget

A chain is ≤ 8 elements × 2 tool invocations (instar microVM
≈ 151 ms; qemu-img a few ms) + per-step `cmp` over ≤ 64M sparse
files + end-of-chain check/compare. Estimate ≲ 4 s per snapshot
iteration — comparable to `op_commit`. At 1/11 of op picks the
nightly's 1000 iterations stay well inside the 180-minute
timeout.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 13a | high | fable | in-worktree | Implement per the Design section: `_snapshot_chain_picker`, `op_snapshot`, `_snapshot_normalize_dates`, the startup qemu version probe, the `--ops` filter, `OPERATIONS` + `run_iteration` dispatch wiring. Follow `op_resize` / `op_commit` style (divergence dicts carry every picker choice + the chain + element index; truncate stderr at 500 chars). The comment table mapping avoided inputs to quirks entries goes above the picker. |
| 13b | medium | fable | in-worktree | Verification. (i) Probe the container's qemu-img version (`docker run … instar-build qemu-img --version` via the existing Makefile devcontainer plumbing or a documented one-off) and record it in the back-brief; set the version gates accordingly. (ii) Rebuild `make instar` from clean source FIRST (probe 5's lesson). (iii) Soak: `--ops snapshot --iterations 500` locally — zero divergences required; report wall-time per iteration. (iv) Can-fire, comparator side: temporarily drop `file.discard=ignore` from the qemu invocation → delete/apply chains must report byte divergences; temporarily skip date normalization → create chains must report divergences; revert both exactly, re-run a 50-iteration smoke. (v) Seeded reproduction: take one soak iteration's seed, re-run with `--seed`, confirm the identical chain replays. |
| 13c | low | fable | in-worktree | Docs + gates + commit. `docs/quirks.md`: new entry for the zero-date `-l` rendering divergence (instar blank DATE vs qemu epoch — degenerate input, found by probe 3; note it is unreachable via qemu-created images and that phase 14 owns any fix decision). Master plan: phase 13 row → Landed; differential-fuzzer success-criterion row annotated. Gates: `make lint`, `pre-commit run --all-files` (no Rust changes — `make test-rust` and `make check-binary-sizes` should be no-ops but run them anyway and say so). Single commit per `~/.claude/CLAUDE.md` conventions: the chain design, the comparator rules and why each normalization exists (cite the probes), the divergence-avoidance table, soak + can-fire results. |

## Agent guidance

### Execution model

**Single Fable agent, working directly in the operator's
`snapshot`-branch worktree** (the phase 12 post-mortem: isolated
agent worktrees spawn on a stale base and permission rules block
fixing them; direct-in-worktree worked cleanly). The agent must
not run `git reset` / `git restore` / `git checkout --` or
anything else that discards state; its only git writes are `git
add` of files it touched and the final commit.

**Permission caveat (operator action before dispatch).** The
phase 12 agent was blocked by the sub-agent Bash allowlist
(`make fuzz-*`, `python3 …` were auto-denied) and correctly
stopped at the verification boundary. This phase's verification
needs at least: `Bash(python3 scripts/differential-fuzz.py *)`,
`Bash(make instar *)` (already allowlisted), `Bash(qemu-img *)`,
`Bash(qemu-io *)`, and the container version probe. Either
pre-approve these in `.claude/settings.local.json`, or expect
the agent to stop after 13a and hand verification back to the
management session (the phase 12 pattern, which worked).

### Management session review checklist

- [ ] Every comparator normalization traces to a probe or quirks
      entry; nothing extra was added to make the soak pass.
- [ ] The avoidance table covers all six catalogue rows and the
      picker enforces each one.
- [ ] 500-iteration soak: zero divergences, per-iteration cost
      sane (≲ 5 s).
- [ ] Can-fire: both deliberate comparator corruptions produced
      divergence reports; reverts exact (`git diff` clean on the
      script afterwards except intended changes).
- [ ] Independent spot-check: re-run a 100-iteration seeded soak
      from the management session; replay one reported seed.
- [ ] No Rust changes; gates green.

## Administration and logistics

### Success criteria

Phase 13 is complete when:

* `--ops snapshot --iterations 500` passes locally with zero
  divergences at sane cost.
* Both can-fire corruptions demonstrably fire and are exactly
  reverted.
* The container's qemu-img version is recorded and both version
  gates are set to match reality.
* The zero-date `-l` quirk is documented; the master plan row is
  Landed; all gates green; single commit.

### Future work created by this phase

- **Zero-date `-l` rendering**: decide in phase 14 whether
  instar should render the epoch like qemu for `date_sec == 0`
  (parity) or keep the blank column (arguably clearer); either
  way the quirks entry from 13c stands.
- **17-plus-snapshot images**: when the 16-snapshot v1 cap
  lifts, drop the picker's live-count guard and add cap-edge
  chains.
- **Cross-version differential soak**: the fuzzer compares
  against one qemu-img; running the chain against the full
  `instar-testdata/qemu-img-binaries/` matrix is a possible
  follow-on, but the per-version behaviour differences (delete
  matcher, list format) make it a research task, not a lane.

### Bugs fixed during this work

None expected in production code (this phase changes only the
fuzzer script and docs). Any soak divergence is a stop-and-report
finding against phases 5–9.

### Documentation index maintenance

This is a phase plan, not a master plan: **not** added to
`docs/plans/order.yml`. The master plan's Execution table already
links this file; step 13c flips the row to Landed.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with it — including your reading of the
divergence-avoidance table against `docs/quirks.md`, the
container qemu-img version once probed, and the observed soak
cost per iteration.
