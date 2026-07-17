# Host subject phase 2: kerbside adoption, CI proof, docs

This is the detailed plan for phase 2 of
[PLAN-host-subject.md](/components/kerbside/plans/PLAN-host-subject/). Read the
master plan and the phase-1 plan first; their Situation
sections are assumed knowledge. Phase 1 is implemented on
ryll branch `host-subject-verifier`; **step 2a of this
phase is blocked until that merges to ryll develop** (the
pin must reference reachable develop history). Steps
2b-2e are not blocked and may be prepared in parallel,
but the CI proof only turns enforcing once 2a lands.

## Prompt

All implementation for this phase lands in this repo
(`shakenfist/kerbside`), branch `host-subject-enforcement`
or a follow-on branch at the operator's discretion.
Ground every claim in the code rather than this plan's
paraphrase; the phase-2 research below was verified
2026-07-17 but the tree moves.

## Repository and branch logistics

- Repo: `shakenfist/kerbside`. One commit per step below.
- Step 2a additionally edits `rust/kerbside-proxy/`
  (Cargo pin, comment, tests) — Rust gates are
  `cargo fmt --check`, `cargo clippy -- -D warnings`,
  `cargo test` (the rust.yml workflow runs these; local
  runs may use the host toolchain via the venv-free
  `cargo` in CI or Docker per the operator's preference).
- Python/docs changes gate on
  `pre-commit run --all-files` (which runs flake8 and
  the unit tests via tox).

## Situation (phase-2 research, verified 2026-07-17)

- **The audit path is already correct.** On backend
  connect failure the proxy warns and emits an audit
  event `"Hypervisor connection failed: {err}"`
  (`backend.rs:245-275`), matching the Python proxy's
  generic form. A subject mismatch arrives as a TLS
  handshake failure whose ryll error string names
  `host_subject`; `is_need_secured` (`backend.rs:54-59`)
  does not match handshake failures (proven by the
  existing test at `backend.rs:298-304`), so no bogus
  retry. This settles the master plan's audit open
  question: reuse the generic message — no code change
  needed, only test coverage.
- **The `need_secured` retry rebuilds the client**
  (`backend.rs:86-123`): `secure_config.tls_port =
  Some(target.secure_port)` then a fresh
  `SpiceClient::new`, so the pin reaches the TLS attempt.
- **The pin**: `rust/kerbside-proxy/Cargo.toml:19`,
  `rev = "1c6f19f3..."` (pre-enforcement). Bump target is
  the ryll develop merge commit of the
  `host-subject-verifier` PR. The stale
  `TODO(host_subject)` comment block is
  `backend.rs:197-202` (the empty→None mapping under it
  stays). Existing mapping test at `backend.rs:356-369`.
  Nothing Python-side references the rev.
- **The direct-qemu functional lane backend is
  plaintext.** `start-qemu.sh:87` passes only
  `port=...,password-secret=...`; no `tls-port`/x509
  plumbing exists. `generate-tls.sh` mints only the
  proxy's client-facing certs (CA CN=kerbside-ci-ca;
  proxy cert `/C=US/O=Kerbside CI/CN=kerbside-ci`). No
  qemu server certificate exists anywhere.
- **The standalone Rust-proxy harness is the right test
  home.** `tools/direct-qemu/verify-rust-proxy.sh`
  (actions `up|down|assert|assert-firewall`) manages its
  own qemu + mock-gRPC-server + proxy;
  `mock-grpc-server.py` already has `--ca-cert` and
  `--host-subject` flags mapped to the Target proto
  (`mock-grpc-server.py:290-297,176-186`) — currently
  never threaded through `MOCK_ARGS`
  (`verify-rust-proxy.sh:451-462`, `--secure-port 0`
  hardcoded). Its `assert` action is metrics-based
  (authorized ≥ 1, bytes relayed > 0 both directions);
  its mock logs every audit message
  (`mock-grpc-server.py:204-210`), so a refusal can be
  asserted by metrics (no bytes) plus grepping the mock
  log for `Hypervisor connection failed`.
  `assert-firewall` (`FIREWALL_EXPECT=clean|deny`) is
  the precedent for parameterised expectations.
- **qemu TLS mechanics** (runner is debian-12, QEMU 7.2,
  supports all of this): `-spice
  port=P,tls-port=T,tls-channel=all,x509-dir=DIR,...`
  where DIR contains exactly `ca-cert.pem`,
  `server-cert.pem`, `server-key.pem`. With
  `tls-channel=all`, a plaintext connect on P receives
  the SPICE `need_secured` reply — exercising the
  proxy's real retry path — and the TLS connect on T
  presents `server-cert.pem`, whose DN is whatever we
  mint. This is the canonical hypervisor deployment
  shape.
- **Docs**: `docs/proxy-architecture.md:57-60` claims
  enforcement (false today, true after 2a — wording
  still needs the "only when host_subject is set"
  nuance). `ARCHITECTURE.md:349-352` says subject
  pinning "is future work" (true today, false after 2a).
  `docs/console-sources.md:146` is a bare field
  description worth an "enforced" note.
  `PLAN-rust-proxy.md`'s future-work entry for backend
  host_subject enforcement must be marked done pointing
  at the master plan (a master-plan success criterion).
- **oVirt open question**: the oVirt CI lane
  (`functional-tests.yml` `ovirt_matrix`) never reads
  `host.certificate.subject` — its console test
  (`tools/test-ovirt-console.py`) handshakes the
  hypervisor's plaintext port directly, bypassing the
  proxy and the scraper. Answering the format question
  needs the value captured from the live engine during
  that lane.

## Mission

Adopt the phase-1 enforcement in kerbside: bump the pin,
true up the comment and tests, prove both the accept and
refuse paths end-to-end against a real TLS-enabled qemu
in CI, correct the documentation, and capture the oVirt
subject format so the remaining open question closes on
data rather than guesswork.

## Design decisions

1. **The CI proof lives in the standalone harness, TLS
   from the start of the lane.** `generate-tls.sh` gains
   a qemu server certificate (same CA; fixed subject
   `C=US, O=Kerbside CI, CN=qemu-hv` — chosen to include
   a space after a comma in the *generation* input but
   stored/pinned in spice-common form
   `C=US,O=Kerbside CI,CN=qemu-hv`); `start-qemu.sh`
   gains optional `--tls-port`/`--x509-dir` arguments
   appending `tls-port=`, `tls-channel=all`, and
   `x509-dir=` to the `-spice` line;
   `verify-rust-proxy.sh` threads `--secure-port`,
   `--ca-cert`, and `--host-subject` into `MOCK_ARGS`.
   With `tls-channel=all` the proxy's plaintext attempt
   receives `need_secured` and retries on the TLS port —
   the full production path, not a shortcut.
2. **Positive and negative assertions, both gating.**
   Positive: the existing `assert` action against the
   TLS-enabled lane (authorized + bytes both directions
   through the TLS backend, pinned subject matching).
   Negative: a new `HOST_SUBJECT_EXPECT=mismatch` mode
   (precedent: `FIREWALL_EXPECT`) that points the mock at
   a deliberately wrong subject (`CN=not-the-hypervisor`)
   and asserts: authorized ≥ 1 (authorization precedes
   backend connect), zero bytes relayed, and the mock's
   gRPC log contains an audit message matching
   `Hypervisor connection failed` and `host_subject`.
   Also assert the *positive* lane's mock log does NOT
   contain a connect failure, so the negative signal
   stays meaningful.
3. **Workflow wiring.** The direct-qemu functional
   workflow gains steps invoking the harness in TLS mode
   (up → assert → mismatch-assert → down), placed after
   the existing lane teardown so port collisions are
   impossible; artifact upload extended with the
   harness's proxy/mock logs on failure.
4. **Pin bump is exact and minimal.** `rev = "<ryll
   develop merge commit>"`, `cargo update -p
   shakenfist-spice-protocol`, delete the six stale TODO
   lines (`backend.rs:197-202`) replacing them with a
   short comment stating enforcement semantics and
   pointing at the ryll crate; extend backend tests with
   a case asserting a `host_subject`-mentioning handshake
   error is NOT `is_need_secured` (parallel to the
   existing bad-certificate case).
5. **Docs tell one story.** `proxy-architecture.md`
   wording adjusted to "when the console carries a
   host_subject, the backend certificate's subject must
   match it (spice-common semantics; substitutes for
   hostname verification)"; `ARCHITECTURE.md`'s "future
   work" sentence replaced with the enforced behaviour
   and a pointer to the master plan;
   `console-sources.md` field row gains "enforced";
   `PLAN-rust-proxy.md` future-work entry marked done.
6. **oVirt capture is diagnostic, not gating.** A small
   step in the oVirt lane (after engine deploy) queries
   the engine for the host certificate subject via the
   same SDK call path the scraper uses
   (`hosts_service.list(...)[0].certificate.subject`)
   and prints it plus a verdict from a parse attempt
   (reusing the documented grammar). Non-gating: its
   job is to put the real string in CI logs so the
   normalisation question is answered with data. If the
   format proves incompatible, scrape-time normalisation
   in `ovirt.py` becomes a recorded follow-up, not a
   silent behaviour.

## Open questions (to settle during the phase)

- Whether `backend.rs` needs a distinct metrics counter
  for backend connect failures (would make the negative
  assertion cleaner than log-grepping). Lean: not in
  this phase; the audit log assertion suffices and a
  counter is a one-line follow-up if the assertion
  proves flaky.
- Exact qemu x509-dir file naming vs explicit
  `x509-key-file=`/`x509-cert-file=`/`x509-cacert-file=`
  arguments — implementer verifies against QEMU 7.2
  behaviour on the runner (x509-dir with the three fixed
  filenames is expected to work).
- Whether the ryll error text for a subject mismatch
  (which the audit message embeds) is stable enough to
  grep for `host_subject` — verify against the phase-1
  implementation (`warn!("TLS: rejecting certificate:
  pinned host_subject {expected}: {e}")` is the proxy-side
  log; the error reaching the audit message is the
  connect error chain — confirm what `{err}` renders).

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | medium | sonnet | none | BLOCKED until the ryll PR merges. Bump `rust/kerbside-proxy/Cargo.toml:19` `rev` to the ryll develop merge commit and run `cargo update -p shakenfist-spice-protocol`; replace the stale `TODO(host_subject)` comment (`backend.rs:197-202`) with a short factual comment (enforcement now lives in the ryll crate's verifier; empty string still maps to None = unpinned); extend `backend.rs` tests: a handshake-failure error string mentioning host_subject is not `is_need_secured`, and `build_config` maps a populated subject to `Some` (exists — extend, don't duplicate). Gates: fmt/clippy/test on the proxy crate. |
| 2b | high | opus | none | The harness TLS fixture, per design decisions 1-2: qemu server cert in `generate-tls.sh` (same CA, subject exactly `C=US,O=Kerbside CI,CN=qemu-hv`, no SANs needed, x509-dir layout `ca-cert.pem`/`server-cert.pem`/`server-key.pem`); `--tls-port`/`--x509-dir` in `start-qemu.sh` appending `tls-port=,tls-channel=all,x509-dir=` to the `-spice` line (keep the plaintext port — the need_secured path is the point); thread `--secure-port`, `--ca-cert` (PEM contents, matching how Target.ca_cert is consumed — check `mock-grpc-server.py` and `backend.rs` build_config for whether it expects inline PEM), `--host-subject` through `verify-rust-proxy.sh` `MOCK_ARGS`; add `HOST_SUBJECT_EXPECT=match|mismatch` handling with the assertions in design decision 2. Follow the harness's existing style (actions, env-var config, log-tail-on-failure). Validate locally end-to-end: up, assert, mismatch variant, down (KVM available on this host). NOTE: until step 2a lands, the pinned ryll crate does not enforce — the mismatch assertion will FAIL against the old pin; develop and validate this step with a locally-bumped (uncommitted) pin, then commit only the harness changes. |
| 2c | medium | sonnet | none | Workflow wiring per design decision 3: new steps in `.github/workflows/direct-qemu-functional.yml` after the existing lane's teardown, invoking the harness TLS lane (up/assert/mismatch/down), with log artifacts on failure. Scripts >5 lines belong in `tools/` per repo convention — the workflow steps should only call the harness. |
| 2d | low | sonnet | none | Docs sweep per design decision 5 (four files: `docs/proxy-architecture.md`, `ARCHITECTURE.md`, `docs/console-sources.md`, `docs/plans/PLAN-rust-proxy.md` future-work entry). Small factual edits matching surrounding style. |
| 2e | medium | sonnet | none | oVirt subject capture per design decision 6: a small `tools/` script (Python, ovirt-engine-sdk already used by `test-ovirt-console.py` — check its connection bootstrap and reuse it) printing each host's `certificate.subject` and a parse verdict against the documented grammar; a non-gating step in the oVirt lane invoking it. The parse verdict can be a minimal Python re-implementation of the grammar (documented as diagnostic-only, the Rust module is the enforcement source of truth). |

Step order: 2b, 2c, 2d, 2e may proceed before 2a merges
(2b validated with a local pin bump); commit 2a as soon
as the ryll merge commit exists, then confirm the full
lane goes green on the PR.

## Success criteria

* The direct-qemu functional workflow proves, on every
  PR: a session relays through a TLS-enabled qemu backend
  reached via the real `need_secured` retry with the
  correct subject pinned; and a wrong pin refuses the
  backend with zero bytes relayed and an audit event
  `Hypervisor connection failed: ...host_subject...`.
* `rust/kerbside-proxy` pins a ryll develop rev
  containing enforcement; the stale TODO is gone; fmt,
  clippy, and tests pass.
* The four docs tell the enforced story;
  `PLAN-rust-proxy.md`'s future-work entry is closed
  with a pointer here.
* The oVirt lane's logs contain the live
  `host.certificate.subject` string and its parse
  verdict, closing the master plan's format question
  with data (any incompatibility recorded as follow-up
  work in this plan's Future work).
* `pre-commit run --all-files` passes.

## Future work (recorded, not in this phase)

* A backend connect-failure metrics counter if the
  log-based negative assertion proves flaky.
* Scrape-time normalisation in `ovirt.py` if 2e shows
  oVirt's format diverges from the spice-common grammar.
* Wiring the oVirt lane's console test through the
  proxy with `secure_port`/`host_subject` (true
  cross-hypervisor enforcement proof).

## Outcome

Implemented 2026-07-17 on kerbside branch
`host-subject-enforcement` (five commits, one per step):

- `c31791e` (2a) — pin bumped to ryll develop `5f986e9`
  (ryll PR #166); stale TODO replaced; two new backend
  tests (subject-mismatch error is not `need_secured`;
  escaped commas pass through unmangled). The new
  dependency subtree raises the crate's rustc floor to
  1.88; gates ran green in `rust:trixie`.
- `82d2290` (2b) — the harness TLS fixture: qemu server
  cert (no SANs, subject `C=US,O=Kerbside CI,CN=qemu-hv`),
  `tls-channel=default` so the plaintext port answers
  `need_secured`, mock Target threading, and the
  `assert-host-subject` action. Validated end to end
  locally in both modes: the match run relayed through
  the TLS backend after per-channel `need_secured`
  retries; the mismatch run refused with zero bytes, the
  audit event, and the proxy's subject-refusal line.
- `c126dec` (2c) — gating workflow step running both
  modes via `run-host-subject-checks.sh` after the main
  lane's teardown (same default ports), with a dedicated
  artifact bundle.
- `6210b58` (2d) — docs sweep; PLAN-rust-proxy's
  future-work entry closed.
- `1b41869` (2e) — `dump-ovirt-host-subject.py` plus the
  non-gating oVirt-lane step; the answer to the format
  question arrives in the next oVirt lane run's logs.

Findings during execution:

- The audit message for a subject refusal renders
  rustls's error: `Hypervisor connection failed: invalid
  peer certificate: NotValidForName` — it does not name
  host_subject. The CI assertion therefore combines the
  audit event with the proxy log's
  `pinned host_subject ... does not match` line.
  Recorded as future work: a more self-explanatory
  audit message (annotate in the proxy, or a
  descriptive error variant in the ryll crate).
- `tls-channel=default` (not `all`) is the QEMU
  spelling for "TLS required on every channel".

CI proof (PR #114, run 29558338498 and siblings,
2026-07-17): every check green, including the direct-qemu
lane with the new gating host-subject steps. The oVirt
diagnostic reported the live engine's value:
`O=local,CN=ovirt.local` — verdict PARSES under the
spice-common grammar (comma-separated, no spaces, short
names). oVirt values need no scrape-time normalisation;
the master plan's format question is closed with data.

Status: phase complete on the branch; merged when PR #114
merges.

## Back brief

Before executing any step of this plan, back brief the
operator on the intended approach and any deviations
from this plan discovered during implementation.
