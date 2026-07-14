# qcow2 write infrastructure — phase 01: semantics pin

Parent:
[PLAN-qcow2-write-infrastructure.md](/components/instar/plans/PLAN-qcow2-write-infrastructure/).
Planned at high effort.

## Scope

Investigation-only phase: no production code changes. Four
questions are settled empirically and by code inventory, and the
findings are recorded back into the master plan (a new
"Findings: phase 1 semantics pin" section under Agent guidance,
following the precedent of PLAN-bench.md's OQ4 findings section,
with live probe transcripts). Probe scripts live in the session
scratchpad, not the tree — the findings write-up must therefore
be self-contained (fixture recipes, exact commands, observed
output).

The phase closes with master-plan updates: OQ1-OQ3 resolved (or
explicitly narrowed), the phase 2 row confirmed live or collapsed
into a documentation note, and — if a live corruption defect is
confirmed — a GitHub issue drafted for the operator to review
before filing.

Facts already established at master-plan time (do not re-derive;
verify only if a probe contradicts them):

* commit has no `nb_snapshots` gate and no COPIED/refcount check
  before overwriting backing clusters
  (`src/operations/commit/src/main.rs`, verified by grep; blind
  overwrite at :772). rebase likewise has no snapshot gate
  (`src/operations/rebase/src/main.rs`,
  `src/crates/rebase/src/qcow2.rs`, verified by grep).
* bench refuses `nb_snapshots > 0`
  (`src/operations/bench/src/main.rs:642`).
* The commit / rebase differential-fuzz oracles are **normalised
  `qemu-img info --output=json` comparison**, not byte identity:
  `op_commit` compares info JSON on both the resulting overlay
  and backing (`scripts/differential-fuzz.py:2837` and the
  comparison loop near :2960); `op_rebase` compares info JSON
  (:2595). `op_bench` is sha256 on raw but `qemu-img compare` +
  `qemu-img check` metrics on qcow2 (:2429, contract at
  :2437-2445).
* The cross-version baselines for commit and rebase are
  info-equivalence, not byte:
  `instar-testdata/expected-outputs/commit-backing-info-json`,
  `commit-overlay-info-json`, `rebase-info-json`.
* Pinned static qemu-img builds live at
  `../instar-testdata/qemu-img-binaries/x86_64/<version>/qemu-img`
  covering 6.0.0 through 10.2.0 (79 versions); the system
  qemu-img is 10.0.8.

### Q1 — qemu-img commit into a snapshot-bearing backing

What does `qemu-img commit` do when the backing file has internal
snapshots, and what does `instar commit` do to the same fixture?

Fixture recipe (one probe script parameterised over the matrix):

1. `qemu-img create -f qcow2 -o cluster_size=<cs> base.qcow2 64M`.
2. Write pattern A into a known region via `qemu-io -c 'write -P
   0xaa <off> <len>' base.qcow2`.
3. `qemu-img snapshot -c snap1 base.qcow2` — the A clusters are
   now snapshot-shared (refcount 2, COPIED clear).
4. Optionally write pattern B over part of A's region (post-
   snapshot COW inside base, exercising a mixed refcount state).
5. `qemu-img create -f qcow2 -b base.qcow2 -F qcow2
   overlay.qcow2` and write pattern C into the overlay:
   one extent overlapping A's region (forces the commit to write
   into snapshot-shared clusters), one extent in never-allocated
   space (forces fresh allocation).
6. Duplicate the whole directory byte-for-byte for the instar
   side before any commit runs.

Pre-commit record, per side: sha256 of base; `qemu-img map
--output=json base.qcow2`; snap1's full content (apply `snap1` on
a *copy* of base, `qemu-img convert` it to raw, sha256).

Probe, qemu side: `qemu-img commit overlay.qcow2`. Post-commit
record: exit code; `qemu-img check` metrics on base; `qemu-img
snapshot -l` still lists snap1; snap1's content re-extracted the
same way and compared to the pre-commit extraction (**the core
question: preserved or corrupted?**); active content of base
converted to raw and compared against the expected merge; whether
base's file length grew (COW allocating new clusters) and where
the committed clusters landed (map diff).

Probe, instar side: identical steps with `instar commit`. If
instar corrupts snap1 (expected from code reading: the blind
overwrite lands in the shared cluster), capture the full
divergence: check metrics (corruptions/leaks), snap1 content
diff, active content diff vs qemu's result.

Matrix: `cs` ∈ {512, 65536}; with and without step 4; committed
extent overlapping-shared vs fresh-only (the fresh-only row is
the control — both tools should agree there); qemu versions
{6.2.0, 7.2.22, 8.2.10, 9.2.4, 10.2.0, system 10.0.8} from the
pinned-binaries tree (fixture generation and observation with the
same version as the commit under test). instar is the current
`develop`-built binary throughout (`make instar`; see AGENTS.md
for build and run conventions).

Also probe the second-order case: commit when the **overlay**
(not the backing) has internal snapshots — qemu-img may refuse;
record the message and exit code, and what instar does.

### Q2 — rebase safe mode on a snapshot-bearing overlay

Same shape as Q1. Fixture: `base_old.qcow2` and `base_new.qcow2`
with differing content in known regions; overlay backed by
base_old; write into the overlay; `qemu-img snapshot -c snap1
overlay.qcow2` so the overlay's active L1/L2/data are
snapshot-shared; then `qemu-img rebase -b base_new.qcow2 -F qcow2
overlay.qcow2` (safe mode, no `-u`).

The questions: does qemu refuse, or proceed and COW the shared
L2 tables / data clusters it must rewrite (observable as new
allocations in the map diff and an unchanged snap1 content
extraction)? What does `instar rebase` do to the identical twin
fixture — refuse, corrupt snap1, or match? Matrix: cluster sizes
{512, 65536}, versions as Q1.

### Q3 — parity-oracle inventory and COW placement determinism

Two deliverables:

1. **An oracle inventory table** (goes into the master-plan
   findings) listing, for each v1 consumer (commit, rebase,
   bench `-w`), every parity surface with file:line — unit
   tests, integration tests (`tests/test_commit.py`,
   `tests/test_rebase.py`, `tests/test_bench.py` including
   `KNOWN_BENCH_DIVERGENCES`), cross-version baselines
   (`instar-testdata/expected-outputs/*`), differential-fuzz
   oracle (`scripts/differential-fuzz.py` op functions), and any
   docs/quirks.md entries — each classified as byte-identity /
   virtual-content (`qemu-img compare`) / info-equivalence /
   check-clean. The master plan's OQ3 assumed byte-parity
   oracles for commit and rebase; initial inventory during
   planning found info-equivalence instead, which if confirmed
   **widens** the layout freedom available to COW and refcount
   growth. The inventory must be exhaustive so the conclusion is
   safe to build on.
2. **A COW placement determinism probe**: on the Q1 fixture,
   run `qemu-img commit` N=5 times on byte-identical copies
   under one version — is the resulting base byte-identical
   run-to-run? Then across the Q1 version matrix — is placement
   stable version-to-version? This decides whether byte-parity
   with qemu COW is even *achievable* if a future oracle demands
   it, and therefore how OQ3's recommendation is worded.

The synthesis must also state the **migration-proof oracle** for
phases 4-6: regardless of qemu-parity strictness, the refactor
phases prove byte-invisibility as instar-before vs instar-after
(same fixture corpus through the pre-migration and post-migration
instar binaries, sha256 equality on every output), which is
independent of qemu layout freedom. Confirm a fixture corpus for
that proof exists or specify what phase 4 must build.

### Q4 — memory budget and API-shape survey

Code-reading only. Tabulate, for the three guest ops to be
migrated (`src/operations/commit/src/main.rs`,
`src/operations/rebase/src/main.rs` scratch map at :57-95,
`src/operations/bench/src/main.rs` scratch map at :57-165):

* every scratch region (name, base, limit, static asserts),
  including staged L2 sets, staged refblocks
  (`WRITE_REFBLOCKS_LIMIT` etc.), bounce buffers and the
  bump-allocator heap boundary;
* current guest binary size vs its cap
  (`scripts/check-binary-sizes.sh`, and note the .bss-aware size
  lint history — the amend HEADER_MISMATCH incident — as the
  constraint class to respect);
* whether a shared staging struct sized for the v1 envelope plus
  a one-cluster COW bounce buffer (worst case 2 MiB at
  `MAX_CLUSTER_SIZE`) fits every op's existing map **without
  moving regions**, or which op needs its map re-laid;
* a recommendation for master-plan OQ4 (step-program buffer vs
  incremental query API) with arithmetic: worst-case steps per
  cluster write (COW read, zero/copy fill, data write, L2 patch,
  L1 patch, refcount RMWs, barriers) × a bounded in-flight window
  → bytes of step buffer, compared against available scratch.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | high | default (Fable) | none | Q1 probe. Write a parameterised probe script in the session scratchpad implementing the Q1 fixture recipe, record matrix and observation set exactly as specified in Scope §Q1 (six qemu versions from `../instar-testdata/qemu-img-binaries/x86_64/<v>/qemu-img`, instar from `make instar`). Run the full matrix, emit a per-row verdict table (qemu behaviour; instar behaviour; agree/diverge/corrupt), and capture representative raw transcripts for the findings write-up. The core deliverable is the answer to "does instar commit corrupt backing-file snapshots that qemu preserves?" with evidence. Do not modify tree files. |
| 1b | high | default (Fable) | none | Q2 probe. Same discipline as 1a with the Scope §Q2 fixture (snapshot-bearing overlay, safe-mode rebase to a differing backing). Deliverables: per-row verdict table, transcripts, and the answer to whether instar rebase corrupts overlay snapshots or diverges from qemu. Reuse 1a's probe scaffolding where practical. |
| 1c | high | default (Fable) | none | Q3. Produce the exhaustive oracle inventory table per Scope §Q3 (read `tests/test_commit.py`, `tests/test_rebase.py`, `tests/test_bench.py`, `scripts/differential-fuzz.py` op_commit/:2837 op_rebase/:2595 op_bench/:2429, `../instar-testdata/expected-outputs/{commit-backing,commit-overlay,rebase}-info-json` generation in instar-testdata's generate-baselines.py, docs/quirks.md) with file:line and a strictness classification per surface. Then run the COW determinism probe (N=5 same-version repeats + cross-version sweep on 1a's overlapping-shared fixture, sha256 of resulting base). Conclude with a written recommendation resolving master-plan OQ3, and the phases 4-6 migration-proof oracle statement (instar-before vs instar-after byte equality; name the fixture corpus or specify what phase 4 must build). |
| 1d | medium | default (Fable) | none | Q4. Code-reading survey per Scope §Q4: scratch-region tables for the commit/rebase/bench guest ops with file:line, binary sizes vs `scripts/check-binary-sizes.sh` caps, feasibility verdict for a shared staging struct + 2 MiB COW bounce buffer per op, and the OQ4 API-shape recommendation with the step-buffer arithmetic. No code changes; output is a findings section ready to paste. |
| 1e | medium | default (Fable) | none | Synthesis. Fold 1a-1d outputs into the master plan: add a "Findings: phase 1 semantics pin" section under Agent guidance (tables + representative transcripts, self-contained since probe scripts are ephemeral); mark OQ1-OQ3 resolved with one-line pointers to the findings; update OQ4/OQ5 with 1d's recommendation; set the phase 1 row to Complete and the phase 2 row to live (with a one-line defect summary) or collapsed (with the refusal/match evidence); draft — do not file — a GitHub issue body if a corruption defect is confirmed, for operator review. Update the plans index status column. One commit for the documentation update, following the repo commit-message conventions. |

Steps 1a, 1b and 1d are independent and can run concurrently;
1c's determinism probe depends on 1a's fixture scaffolding
(sequence it after 1a or have it rebuild the one fixture it
needs); 1e depends on all of them.

## Review checklist deltas

Standard checklist from PLAN-TEMPLATE.md, minus the build/test
gates for 1a-1d (no tree changes to build), plus:

* Confirm no probe artefacts or scripts were committed to the
  tree; the only tree change from this phase is the 1e
  documentation commit.
* Cross-check the 1a/1b verdict tables against the raw
  transcripts before accepting a "corruption confirmed" or "no
  defect" conclusion — the conclusion gates whether phase 2
  exists and whether an issue is filed.
* Verify the master-plan findings section is self-contained
  (fixture recipes and commands reproducible without the
  scratchpad).
* `pre-commit run --all-files` passes on the 1e commit.
