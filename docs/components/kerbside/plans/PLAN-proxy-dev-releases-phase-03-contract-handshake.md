# Proxy dev releases phase 3: the contract handshake

Master plan: `PLAN-proxy-dev-releases.md`. This phase embeds a
sha256 of `kerbside/rpc/kerbside.proto` in both the Python package
(a committed constant written by `tools/gen-protos.sh`) and the
Rust binary (a `build.rs` compile-time env), and makes the daemon
compare them before launching the proxy — refusing with a
remediation-listing message on mismatch. This is the backstop that
makes sparse dev-wheel publishing safe: any skew the phase 1 path
filter lets through (or the merge-to-bootstrap window creates)
becomes a loud startup refusal instead of a subtle protocol
failure.

Planning effort: high (per the master plan — the change spans the
gRPC contract convention, build.rs, clap, and the supervisor
launch path across two languages).

## Scope

In scope:

* `tools/gen-protos.sh`: additionally write
  `kerbside/rpc/contract.py` carrying the proto's sha256.
* `kerbside/rpc/contract.py` (new, generated, committed) and a
  unit test pinning it to the actual proto bytes.
* `rust/kerbside-proxy/`: `sha2` build-dependency, `build.rs`
  hash embed, a `--contract-hash` print-and-exit flag, a unit
  test.
* `kerbside/proxy_supervisor.py`: pre-launch handshake, refusal
  message, `KERBSIDE_SKIP_CONTRACT_CHECK` escape hatch, unit
  tests in `kerbside/tests/unit/test_proxy_supervisor.py`.
* End-to-end verification against both the freshly built binary
  (hashes match) and the released 0.4.0 wheel binary (predates
  the flag — refusal path), run in the management session.

Out of scope: documentation beyond code comments (phase 4 covers
AGENTS.md / ARCHITECTURE.md / docs, and must document the two env
vars); the publish workflow and specifier (phases 1-2, done);
pruning (phase 5).

## What the survey found

Verified against the tree on 2026-08-14. **The master plan's
phase 3 sketch contains no false claims**; the only correction it
needed was resolving its "refuses/warns" hedge to "refuses", which
open question 1's operator decision already settled. Specifics:

* `tools/gen-protos.sh` regenerates the checked-in stubs into
  `kerbside/rpc/` and is invoked as `tox -egenprotos`
  (tox.ini:50-58 pins the toolchain for deterministic output).
  Extending it keeps a single regeneration entrypoint.
* `kerbside/rpc/` contains `__init__.py`, the proto, the four
  generated stub files, `server.py` and `servicer.py`; a new
  generated `contract.py` fits the existing convention of
  committed generated artifacts.
* The kerbside wheel does not ship the `.proto`
  (`[tool.setuptools] packages` only, pyproject.toml:132-133), so
  hashing must happen at generation/build time, not runtime —
  confirming the master plan's committed-constant approach.
* `rust/kerbside-proxy/build.rs` already declares
  `cargo:rerun-if-changed=<proto>`, so a compile-time hash embed
  re-derives whenever the proto changes with no extra plumbing.
  `[build-dependencies]` currently carries `tonic-prost-build`
  and `protoc-bin-vendored` (Cargo.toml:90-95); `sha2` joins
  them.
* `main.rs` uses derive-style clap (`struct Args`, main.rs:74+)
  with long flags only — a `--contract-hash` bool flag matches
  the house style. `Args::parse()` happens at main.rs:133 before
  logging init, so the flag can print-and-exit cleanly.
* `kerbside/proxy_supervisor.py::launch_rust_proxy()` (line ~97)
  is `find_proxy_bin()` → `build_proxy_argv()` → `Popen`; the
  handshake slots between the first and last of those.
  `kerbside/tests/unit/test_proxy_supervisor.py` already exists
  for the new tests.
* Phase 2's completion was spot-checked (committed floor present
  and parseable; generalised stamp branch present; tree clean)
  and its Execution-table status flipped to implemented in this
  planning commit. origin/develop has not moved since the phase 2
  rebase, though PR #307 entered the merge queue during planning
  — a final rebase before the eventual PR remains on the plan-
  completion checklist.

## Decisions

1. **The contract identity is the sha256 of the proto file's raw
   bytes**, embedded at generation time (Python, committed
   constant) and compile time (Rust, `env!`). No canonicalisation
   or comment-stripping: a comment-only proto edit changes the
   hash and forces a republish, which is a false positive we
   accept — proto edits are rare, and canonicalisation code on
   two sides is its own skew risk. **This is the decision a
   reviewer is most likely to argue with.**
2. **`sha2` is a build-dependency only** on the Rust side —
   compile-time hashing costs nothing at runtime and avoids
   entangling the runtime dependency tree (ring/rustls) in
   hashing duty.
3. **The committed constant is written by `gen-protos.sh`**, not
   by hand, and a unit test (`kerbside/tests/unit/`) recomputes
   the hash from the proto and compares — so a proto edit without
   regeneration fails `tox -epy3` in CI. No new CI job needed.
4. **Refusal semantics** (implements the operator's open-question
   1 decision): on mismatch, `launch_rust_proxy()` raises
   RuntimeError naming BOTH hashes, the binary path, and the
   remediation options (upgrade the kerbside-proxy wheel; rebuild
   `rust/kerbside-proxy`; point `KERBSIDE_PROXY_BIN` at a
   matching binary; set `KERBSIDE_SKIP_CONTRACT_CHECK=1` to
   bypass for debugging). A binary that does not understand
   `--contract-hash` (every release ≤0.4.0) is treated as a
   mismatch with tailored wording ("binary predates the contract
   handshake"). The escape hatch logs a WARNING and launches
   anyway. Sub-decision most worth flagging: hard-refusing old
   binaries is deliberate — current-Python-plus-0.4.0-binary is
   exactly the broken pairing this plan exists to catch, and the
   escape hatch covers the operator who knows better.
5. **The subprocess probe is bounded**: `--contract-hash` runs
   with a 10-second timeout and any failure mode (timeout,
   non-zero exit, garbage output) is a refusal, never a silent
   pass.
6. **All cargo operations run in the rust Docker image** (the
   operator keeps Rust toolchains off the host); briefs give the
   exact pattern, mirroring phase 1's step 1d.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | medium | sonnet | none | Extend `tools/gen-protos.sh` to write `kerbside/rpc/contract.py` after stub generation: a header comment saying the file is generated by tools/gen-protos.sh (run `tox -egenprotos`) and must not be hand-edited, then `CONTRACT_HASH = '<64-hex sha256 of kerbside.proto>'` computed with `sha256sum kerbside.proto \| cut -d' ' -f1` (the script already cd's into kerbside/rpc). Run `tox -egenprotos`; if ANY already-committed generated stub changes beyond the new contract.py, STOP and report the diff rather than committing regeneration noise. Add `kerbside/tests/unit/test_contract.py`: locate the proto relative to the test file, `hashlib.sha256` its bytes, assert equality with `kerbside.rpc.contract.CONTRACT_HASH`, and assert the constant is 64 lowercase hex chars. Follow repo Python style (single quotes, 120-char lines). `tox -epy3` and `pre-commit run --files <touched>` must pass. |
| 3b | high | opus | none | Rust side, all cargo commands via `docker run --rm -v /srv/kasm_profiles/mikal/vscode/src/shakenfist:/srv/kasm_profiles/mikal/vscode/src/shakenfist -w <worktree>/rust/kerbside-proxy rust:latest cargo ...` (host has no Rust toolchain by policy; the mount path matters because the worktree's gitdir and build.rs's `../../kerbside/rpc` both resolve under it). (1) Add `sha2 = "0.10"` to `[build-dependencies]` with a comment. (2) In `build.rs`, after `compile_protos`, read the proto bytes, compute sha256, and emit `cargo:rustc-env=KERBSIDE_CONTRACT_HASH=<hex>`. (3) In `main.rs`, add to `struct Args` a `/// Print the embedded gRPC contract hash and exit.` `#[arg(long)] contract_hash: bool`, and immediately after `Args::parse()` (before logging init): if set, `println!("{}", env!("KERBSIDE_CONTRACT_HASH")); return;`. (4) Add a unit test asserting `env!("KERBSIDE_CONTRACT_HASH")` is 64 chars of lowercase hex. (5) `cargo fmt`, `cargo clippy --all-targets` (warnings clean, matching rust.yml's bar), `cargo test`, and `cargo run --release -- --contract-hash` must print a 64-hex line; report it and leave `target/` alone (gitignored). Set `CARGO_TARGET_DIR` to a path under /tmp inside the container if root-owned files in the worktree would result — check `git status` is clean of new files afterwards. |
| 3c | high | opus | none | In `kerbside/proxy_supervisor.py`: add `SKIP_CONTRACT_CHECK_ENV = 'KERBSIDE_SKIP_CONTRACT_CHECK'`; add `get_binary_contract_hash(bin_path)` running `[bin_path, '--contract-hash']` via `subprocess.run` with `capture_output=True, text=True, timeout=10`, returning the stripped stdout on rc==0, or `None` on any failure (non-zero rc, timeout, empty/malformed output — 64 lowercase hex or bust); add `check_contract(bin_path)` comparing against `kerbside.rpc.contract.CONTRACT_HASH` and raising RuntimeError per plan decision 4 (message must name both hashes — or "unknown (binary does not support --contract-hash; it predates the contract handshake)" — the binary path, and all four remediations including the escape hatch; grep the module's existing find_proxy_bin error for message style). Wire into `launch_rust_proxy()` between `find_proxy_bin()` and `build_proxy_argv()`: if the escape-hatch env var is set to a truthy value, LOG.warning and skip; else `check_contract()`. Extend `kerbside/tests/unit/test_proxy_supervisor.py` following its existing mock style: hash-match launches; mismatch raises with both hashes in the message; flag-unsupported (rc!=0) raises with the predates wording; timeout raises; escape hatch launches despite mismatch and logs a warning. `tox -epy3` and `pre-commit run --files <touched>` must pass. |
| 3d | — | — | — | Verification, management session: (a) using the step 3b container-built binary (rebuild if the 3a-generated constant changed the proto tree state — it must NOT have), run `python3 -c` importing `proxy_supervisor.get_binary_contract_hash` against it and assert equality with `contract.CONTRACT_HASH`; (b) unzip the 0.4.0 wheel already in the scratchpad (from step 2c) and run `check_contract()` against its `kerbside-proxy` binary — expect the RuntimeError with the "predates the contract handshake" wording; (c) with `KERBSIDE_SKIP_CONTRACT_CHECK=1`, the same call must not raise. Record all three transcripts for the PR description. |

## Risks and mitigations

* **`tox -egenprotos` regenerates stubs with unrelated diffs**
  (toolchain drift since the stubs were last committed). Step 3a
  is instructed to stop and report rather than commit noise; the
  management session decides whether a stub-refresh commit is
  warranted separately.
* **Hash divergence from line endings or encoding** between
  `sha256sum` (3a), `sha2` (3b) and `hashlib` (3c/test): all
  three hash the same raw file bytes with no text-mode
  processing, and step 3d(a) proves the Rust and Python values
  agree on the real artifacts before the phase is called done.
* **The probe adds a subprocess exec to daemon startup**: bounded
  by decision 5's timeout; a hung binary fails the launch loudly
  (which is correct — it would have hung as the proxy too).
* **Root-owned droppings from the Docker cargo runs**: the 3b
  brief requires a clean `git status` after; the reviewer
  (management session) re-checks.
* **A deployer with a legitimately old-but-working pairing is
  hard-refused after upgrading the Python side only** (decision 4
  sub-decision): accepted deliberately; the refusal message's
  remediation list and escape hatch are the mitigation, and
  phase 4 documents both env vars.

## Definition of done

* `kerbside/rpc/contract.py` exists, is marked generated, and
  `test_contract.py` (which recomputes the hash from the proto
  bytes) passes — so the constant cannot silently go stale.
* `tox -egenprotos` is idempotent on the committed tree (running
  it changes nothing).
* The container-built binary's `--contract-hash` output equals
  `CONTRACT_HASH` (step 3d(a) transcript recorded).
* The 0.4.0 release binary is refused with the
  predates-the-handshake message, and launches with the escape
  hatch set (3d(b)/(c) transcripts recorded).
* `cargo fmt --check`, `cargo clippy --all-targets` and
  `cargo test` pass in the container; the new Rust test pins the
  embedded hash's shape.
* All new supervisor behaviours are unit-tested (match, mismatch,
  unsupported flag, timeout, escape hatch) and `tox -epy3`
  passes.
* `pre-commit run --all-files` passes; `git status` is clean of
  Docker droppings.
* The master plan's Execution table and `index.md` reflect
  phase 2 as implemented and phase 3 as planned (done in the
  planning commit).

## Back brief

Before executing any step of this plan, back brief the operator on
the plan and how the intended work aligns with it. Gate: if step
3a's `tox -egenprotos` run surfaces unrelated stub regeneration
diffs, pause for the operator before committing anything from that
step.
