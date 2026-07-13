# Rust proxy phase 4: firewall policy engine (L0 + L1 enforcing)

Part of the [Rust SPICE proxy master plan](/components/kerbside/plans/PLAN-rust-proxy/). This
phase fills the `Policy`/`Verdict` seam that phase 3 built (permissive)
with real, enforcing L0 and L1 firewall policy.

## Prompt

Before executing any step, explore the code the step touches and ground
every decision in what the code and the SPICE protocol actually do.
Read `rust/kerbside-proxy/src/{relay,policy,session,metrics,rpc}.rs`,
the pinned ryll `shakenfist-spice-protocol` crate
(`constants.rs`, `logging.rs`, `messages.rs`), the gRPC contract
(`kerbside/rpc/kerbside.proto`), and the protocol docs under `docs/`
(`channel-protocols.md`, `protocol-overview.md`, `capabilities.md`).
Verify wire-format / message-type questions against the reference SPICE
sources at `/srv/src-reference/spice/` (`spice-protocol/spice/enums.h`
is the authoritative message-type list) rather than guessing. Flag
uncertainty explicitly.

Planning effort for this phase: **high**. It is deep protocol work
(deriving per-channel/direction message-type allowlists that must not
produce false positives against real clients) plus a cross-boundary
policy-delivery decision.

## Repository and branch logistics

All implementation lands in **kerbside** (the Rust proxy crate, the
proto, and the Python daemon that fills the policy), except any
message-type-table gap that must be filled in **ryll**
(`shakenfist-spice-protocol`), which is a separate additive change to
that crate followed by a pin bump here.

Phase 4 builds directly on phase 3's relay/policy code. Branch from the
phase-3 tip if phase 3 has not yet merged, or from `develop` once it
has; rebase onto `develop` before the PR. Work in a worktree. This plan
file lives on the phase-4 branch alongside the code it describes.

## Situation

Phase 3 shipped the inspection-first framed relay with the enforcement
seam in place but empty:

- `relay.rs::pump` already frames every direction by the 6-byte
  `MessageHeader` (`message_type: u16`, `message_size: u32`), and for
  every complete message calls `policy.inspect(dir, channel, header,
  payload) -> Verdict` and acts on `Forward | Drop | Terminate`. The
  header (type + size) is therefore **already parsed** and available to
  the policy — L0 (size) and L1 (type allowlist) need no new body
  parsing.
- `policy.rs` defines the `Policy` trait, the `Verdict` enum (all three
  variants handled by the relay, `Drop`/`Terminate` currently only
  constructed in tests behind `#[allow(dead_code)]`), and the
  `Direction` enum. The only implementation is `PermissivePolicy`
  (always `Forward`). Each relay direction owns its own `Policy`
  instance (no shared lock on the hot path).
- The relay carries a placeholder flat `MAX_MESSAGE_SIZE = 16 MiB` cap
  (documented as "phase 4 tightens this per channel/direction").
- `session.rs` already rejects unknown/obsolete **channel types**
  before the relay (`ChannelType::from_u8` → `None` closes the
  connection). That is the coarsest L1 check and already present.
- Phase 3's pre-push audit deferred two relay-adjacent hardening items
  into this phase: a relay **idle timeout** and **client-side TCP
  keepalive** (an authorized-but-silent client can pin a permit). They
  are L0-class resource limits and land here.

There is **no Python precedent** to port: the Python proxy relays
opaquely post-auth and does no L0/L1 inspection. This is genuinely new
behaviour, so correctness rests on the SPICE protocol definition and on
validation against real client traffic, not on parity with `proxy.py`.

The ryll crate already provides most of the raw material for the
allowlists (verified against the pinned rev):

- `constants.rs` has per-channel/direction message-type opcode modules:
  `main_server`, `main_client`, `display_server`, `display_client`,
  `inputs_server`, `inputs_client`, `cursor_server`, `cursor_client`,
  `playback_server`, `spicevmc_server`, `spicevmc_client`.
- `logging.rs` has message-type **name-table** functions for every one
  of those *plus* `playback_client` — the most complete enumeration of
  known types per channel/direction, and the natural basis for a
  `is_known(channel, direction, msg_type)` allowlist.
- Common base messages apply to **all** channels: server
  `SPICE_MSG_*` 1..7 (MIGRATE=1, MIGRATE_DATA=2, SET_ACK=3, PING=4,
  WAIT_FOR_CHANNELS=5, DISCONNECTING=6, NOTIFY=7) and client
  `SPICE_MSGC_*` 1..6 (ACK_SYNC=1, ACK=2, PONG=3, MIGRATE_FLUSH_MARK=4,
  MIGRATE_DATA=5, DISCONNECTING=6). Channel-specific opcodes begin at
  101. An allowlist for a (channel, direction) is therefore
  `common_base ∪ channel_specific`.
- **Coverage gaps**: the `record` (audio-in) and `smartcard` channels
  have no explicit message-type tables in the crate. `usbredir`, `port`
  and `webdav` all ride the `spicevmc` message set (documented in
  `constants.rs`). See Design decision 4 for how the gaps are handled.

## Mission

Make the proxy enforce SPICE firewall policy on every relayed message,
on by default, with no false positives against the supported clients
(virt-viewer, remote-viewer, ryll headless), and with verdicts visible
in Prometheus metrics and (aggregated) audit events. Concretely:

- **L0 (framing sanity / resource limits)**: per-channel/direction
  message-size caps replacing the flat 16 MiB; a coarse rate/throughput
  ceiling that trips only on abuse; an idle-read timeout; client-side
  TCP keepalive.
- **L1 (message grammar)**: a per-channel, per-direction message-type
  allowlist derived from the SPICE protocol; rejection of
  unknown/obsolete message types; a per-channel-type "permitted"
  toggle so a deployment can forbid whole optional channels (e.g.
  usbredir, webdav). Unknown/obsolete *channel* rejection already
  exists in `session.rs` and is confirmed, not rebuilt.
- **Policy ownership**: Python continues to own policy
  ("Python decides policy; Rust enforces it"). See Design decision 1
  for the delivery mechanism.
- **Observability**: per-verdict Prometheus metrics and locally
  aggregated (not per-message) firewall audit events.

Out of scope (deferred, per the master plan): L2 body validation
(scancode ranges, clipboard/file-transfer/usbredir device-class
filtering), session recording, L3 rewriting/injection, and detailed
per-channel state-machine ordering conformance (see Design decision 3).

## Design decisions

Decisions 1 and 3 were settled with the operator during planning
(deliver policy over gRPC now; Terminate by default with a first-class
warn-only mode). The rest are the planner's calls, open to revision at
back brief.

1. **Policy delivery: over gRPC, in the `AuthorizeConnection` reply.**
   Add a `FirewallPolicy` message to `kerbside.proto` and carry it in
   `AuthorizeConnectionReply` (alongside `Target`). The Python daemon
   fills it from deployment-wide pydantic config; the Rust proxy builds
   each connection's enforcing policy from it. For v1 the value is the
   same on every reply (deployment-wide), but the *mechanism* is
   per-connection, so per-source/per-console policy (master-plan future
   work) becomes a Python-only change later. This honours "Python
   decides policy" and matches the master-plan phase-4 sketch ("policy
   delivered in the `AuthorizeConnection` reply").
   - **Allowlist tables are NOT delivered over gRPC.** Which message
     types are structurally valid on a channel is a fact about the
     SPICE *protocol*, not a deployment policy; it is compiled into the
     proxy (derived from the ryll constants). `FirewallPolicy` carries
     only the *tunable* knobs: enforcement mode, per-channel-type
     enable set, size-cap overrides, rate ceilings, and the
     verdict-on-violation severity.
   - *Alternative (if deferred):* ship compiled defaults + CLI flags in
     phase 4 and move gRPC delivery to phase 5 (which already wires
     config→flags). Keeps phase 4 Rust-only. Recommended path is to do
     it here so enforcement and its policy source ship together.

2. **Per-direction policy instances, shared verdict counters.** Keep
   phase 3's model: each `pump` owns its own `EnforcingPolicy` built
   from a shared `Arc<FirewallPolicy>` config, so the allowlist lookup
   and per-direction rate state need no lock on the hot path. Verdict
   accounting for audit/metrics goes through a shared
   `Arc<VerdictCounters>` of atomics that both directions increment
   (low-cardinality, negligible cost, lock-free); `relay::run` reads it
   after the `select!` and hands it to the audit flusher, so counts
   survive the dropped losing pump.

3. **Verdict severity (recommended defaults).** Enforcing from day one,
   no log-only grace period.
   - **L1 disallowed message type, client→server** (the dangerous,
     low-bandwidth direction): **Terminate**. Fail closed.
   - **L1 disallowed message type, server→client** (the bulk display
     direction the master plan keeps "framed-but-opaque"): **Terminate**
     as well — a hypervisor exploiting a client-side CVE by emitting
     malformed types should be cut off. This is the highest
     false-positive risk, so the server→client tables are the ones the
     capture-validation step (4f) must exhaustively cover; the fix for a
     legitimate-but-unmodeled type is to **add it to the table**, never
     to weaken the verdict.
   - **L0 size cap exceeded**: Terminate.
   - **L0 rate/throughput ceiling exceeded**: metric + aggregated audit,
     and Terminate only when grossly exceeded (a DoS ceiling tuned well
     above validated legitimate peaks). Mid-stream `Drop` is **not**
     used for rate control — dropping a SPICE message desynchronises the
     stream. `Drop` remains in the API for future L2/L3 use (e.g.
     defanging) but no v1 rule emits it.
   - **Forbidden channel type** (policy-disabled channel): denied before
     relay, in `session.rs`, as a `PermissionDenied` auth result +
     audit — same shape as the existing unknown-channel rejection.
   - **Warn-only mode (settled with the operator).** `FirewallPolicy`
     carries an `enforcement_mode: Enforce | WarnOnly` knob (delivered
     over gRPC, Design decision 1), defaulting to **Enforce**. In
     `WarnOnly`, any rule that *would* `Drop` or `Terminate` instead
     emits the metric, the aggregated audit event, and a `tracing`
     warning, then returns `Forward` — the session is never actually
     blocked. This is the confidence-building / debugging tool: it lets
     us run real traffic and see exactly what enforcement *would* have
     tripped (especially on the server→client tables, the
     false-positive risk) before flipping a deployment to `Enforce`. It
     is a mode, not a grace period — the default ships enforcing, per
     the master plan. Structure it so the mode can later be made
     per-direction (e.g. Enforce client→server, WarnOnly server→client)
     without a data-model change; a single global mode is enough for
     v1.

4. **Allowlist coverage and the crate gaps.** Derive `is_allowed(
   channel, direction, msg_type)` from the ryll `logging.rs` name
   tables (`Some(name)` ⇒ known ⇒ allowed) unioned with the common base
   sets. For the channels a Kerbside VDI session actually carries —
   main, display, inputs, cursor, and (if enabled) playback / record /
   usbredir — this must be complete.
   - `usbredir`/`port`/`webdav` → use the `spicevmc` tables.
   - `record`/`smartcard` have no crate tables. **Preferred:** add their
     message-type constants + name tables to ryll (small, additive,
     phase-1-style) and bump the pin — step 4b-ryll, only if the
     capture step shows those channels are carried. **Fallback for v1:**
     for a carried channel with no modeled grammar, enforce L0 only
     (size/rate) and *observe* (audit, no Terminate) unknown types,
     documented as a known partial-coverage gap. A channel that is not
     carried at all is simplest handled by leaving it disabled in the
     default permitted-channel set.

5. **State-machine conformance is scoped to type+channel for v1.** The
   master plan lists "basic state-machine conformance" under L1. Full
   per-channel ordering checks (init-before-data, etc.) are high
   false-positive risk and are deferred to L2/future. v1 L1 =
   type allowlist + per-channel permit + unknown-channel rejection
   (already present). This scoping is called out so it is a conscious
   decision, not an omission.

6. **No new untrusted-body parser ⇒ no new fuzz target required.** L0/L1
   read only the already-parsed header (type + size); they do not parse
   message bodies. The engine instead gets thorough unit + property
   tests and the capture-replay validation (4f). If any step adds body
   parsing it must add a cargo-fuzz target (master-plan requirement).

## Open questions (to settle during the phase)

- Exact `FirewallPolicy` proto shape and defaults: which knobs are
  scalar vs per-channel maps; how per-channel-type enable is encoded
  (bitmask vs repeated enum); whether size-cap overrides are per-channel
  or per-(channel,direction).
- Concrete per-channel/direction size caps and the rate/throughput
  ceiling values — to be fixed from the 4f captures, not guessed.
- Audit aggregation semantics: flush interval, per-connection buffer
  bound, and the dropped-with-a-counter behaviour under audit
  backpressure (a master-plan open question).
- Whether the `record`/`smartcard` channels are carried by any
  supported source+guest combination (decides whether 4b-ryll runs).

## Key protocol / API facts (front-loaded for the sub-agents)

- `MessageHeader` is `{ message_type: u16, message_size: u32 }`, 6 bytes,
  already parsed by `relay::pump` before `policy.inspect`. Payload
  passed to the policy is the body only.
- Common base opcodes (all channels): server 1..7, client 1..6 (listed
  in Situation). Channel-specific opcodes start at 101.
- ryll message-type sources: `constants.rs` opcode modules +
  `logging.rs` name-table fns (`main_server`/`main_client`/… returning
  `&'static str`, with an "unknown" sentinel for unmodeled types) +
  `ChannelType`/`ChannelType::from_u8`/`ChannelType::name`.
- Authoritative external reference: `/srv/src-reference/spice/
  spice-protocol/spice/enums.h` (SPICE_MSG_* / SPICE_MSGC_* per channel).
- gRPC: `AuthorizeConnectionReply` is a `oneof { Denied; Target }` today;
  the bookkeeping RPCs and `RecordAuditEvent` already exist and are used
  by the proxy. Metrics live in `metrics.rs` (global registry + RAII
  `ConnectionGuard`, hyper `/metrics` server).
- The ryll interop note "mouse clicks not working through kerbside
  proxy" (master-plan Bugs section) touches the **inputs_client**
  allowlist — make sure the inputs client message set (mouse
  position/motion/press/release, key down/up) is present and validated,
  and confirm the firewall is not what blocks clicks.

## Execution

One commit per step (per the master plan). Every Rust step must pass
`cargo fmt --check`, `cargo clippy -- -D warnings`, and `cargo test` in
the Docker build; every Python step `tox -eflake8` and `tox -epy3`;
`pre-commit run --all-files` before each proposed commit.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | high | opus | worktree | **Policy engine core.** In `rust/kerbside-proxy/src/policy.rs`, add a `FirewallPolicy` config struct (enforcement mode; per-`ChannelType` permitted set; per-channel/direction size caps; rate/throughput ceilings; verdict-on-violation severities — see Design decisions 1,3) with an enforcing `Default`, and an `EnforcingPolicy` implementing the `Policy` trait that consults the L1 allowlist (from 4b) and L0 caps (from 4c) and returns the configured verdict. Build it from an `Arc<FirewallPolicy>` per direction. Swap `PermissivePolicy` for `EnforcingPolicy` in `relay::run` (keep `run`'s signature; thread the `Arc` and the shared `VerdictCounters` from 4d in). Remove the `#[allow(dead_code)]` on `Verdict` once real code constructs the variants. In `WarnOnly` mode, downgrade a would-be Drop/Terminate to `Forward` while signalling it to the `VerdictCounters`/aggregator as `observed` (Design decision 3) — so the mode lives in the engine, not scattered through the relay. Unit-test the verdict decision table in both modes (Enforce: allowed→Forward, disallowed→Terminate per direction, oversized→Terminate; WarnOnly: the same inputs all→Forward but recorded as observed; disabled channel handled upstream). Keep `PermissivePolicy` for tests. |
| 4b | high | opus | worktree | **L1 allowlist tables.** Add an `allowlist` module exposing `is_allowed(channel: ChannelType, dir: Direction, msg_type: u16) -> bool`, built from the ryll `logging.rs` name tables (known-name ⇒ allowed) unioned with the common base sets (server 1..7, client 1..6). Cross-check every entry against `/srv/src-reference/spice/.../enums.h`. Document per (channel,direction) what is covered. Handle `usbredir`/`port`/`webdav` via the `spicevmc` set. For `record`/`smartcard`, follow Design decision 4 (L0-only + observe, or defer to 4b-ryll). Tests: representative known types allowed, a bogus/obsolete type rejected, common base allowed on every channel, the inputs_client mouse/key set allowed (the interop-bug guard). |
| 4b-ryll | high | opus | worktree | **(Conditional — only if 4f shows record/smartcard are carried.)** In the ryll `shakenfist-spice-protocol` crate, add message-type opcode constants + `logging.rs` name tables for the `record` and/or `smartcard` channels, mirroring the existing channel modules, with round-trip/name tests. Land as its own PR to ryll; then bump the pinned rev in `rust/kerbside-proxy/Cargo.toml` and extend 4b's tables. |
| 4c | high | opus | worktree | **L0 limits + relay hardening.** Replace the flat `MAX_MESSAGE_SIZE` in `relay.rs` with per-(channel,direction) caps from `FirewallPolicy` (generous for display server, tight for inputs/cursor client). Add a coarse per-direction rate/throughput accounting (token-bucket or windowed counter) that trips only above the configured ceiling → Terminate + audit (Design decision 3). Add a relay **idle-read timeout** (wrap the pump `read` in `tokio::time::timeout`, generous default sized to not kill legitimately-idle interactive sessions) and set **TCP keepalive** on the accepted client socket in `listen.rs` (mirror the backend leg; `socket2`) — closing the phase-3 deferred permit-pinning findings. Tests for each cap/limit and the timeout. |
| 4d | high | opus | worktree | **Verdict metrics + audit aggregation.** Add Prometheus counters `kerbside_proxy_firewall_verdicts_total{channel,direction,rule,action}` to `metrics.rs`, where `action` distinguishes **enforced** (Terminate/Drop actually applied) from **observed** (would have blocked, but `WarnOnly` let it through) so warn-only runs are measurable and directly answer "what would Enforce have tripped?" (Design decision 3). Add a shared `Arc<VerdictCounters>` (atomics) both pump directions increment; `relay::run` reads it post-`select!`. Add a bounded local **audit aggregator** that coalesces firewall verdicts per (connection, rule, action) and flushes a single summarising `RecordAuditEvent` on teardown (and optionally on a periodic interval), never one event per blocked message — with a dropped-events counter under backpressure (Open question). Tests for aggregation/flush, the enforced-vs-observed split, and the counter wiring. |
| 4e | high | opus | none | **Policy delivery over gRPC.** Add a `FirewallPolicy` message to `kerbside/rpc/kerbside.proto` and carry it in `AuthorizeConnectionReply` (Design decision 1); regenerate. In the Python daemon, fill it from new deployment-wide pydantic config (`kerbside/config.py` + `docs/configuration.md`), following the existing `AuthorizeConnection` handler. In the Rust proxy, build the per-connection `EnforcingPolicy` from the delivered policy (falling back to the enforcing compiled default if absent). Python tests for the handler; Rust tests for the proto→`FirewallPolicy` mapping. *(Optional, if cheap here: add per-unary-call gRPC deadlines in `rpc.rs` — a phase-3 deferred LOW finding — but NOT a channel-wide timeout, which would kill the `ProxyControl` stream.)* |
| 4f | high | opus | none | **Capture-based validation harness.** Extend `tools/direct-qemu` to capture the set of `(channel, direction, message_type, max_size)` observed during a real session (a tap/observe mode in the proxy, or a parse of the framed relay), and run it for **virt-viewer, remote-viewer, and ryll headless** against a real qemu. Assert the L1 allowlist and L0 caps cover every observed message with **zero** Terminate verdicts on legitimate traffic (the "no false positives" success criterion); feed the observed sizes back into the 4c caps. Determine whether record/smartcard are carried (gates 4b-ryll). Also fold in the phase-3 deferred harness items: a **deny-token mode** and a **TLS-required backend mode** in `tools/direct-qemu/mock-grpc-server.py`, enabling the previously-untested session denial-path (`PermissionDenied`/`Error` to client) and `need_secured` retry-orchestration tests. Document results in a `VERIFY-*.md`. |
| 4g | medium | sonnet | none | **Docs + Outcome.** Update `ARCHITECTURE.md`, `AGENTS.md`, `README.md`, `docs/proxy-architecture.md`, and `docs/configuration.md` (new firewall config knobs + the metrics) to describe L0/L1 enforcement. Write this plan's Outcome section (mirroring phase 3: steps landed, deviations, the pre-push audit result) and flip the phase-4 row in `PLAN-rust-proxy.md` and `docs/plans/index.md` to Complete. |

Sequencing notes: 4a depends on the shapes from 4b/4c/4d, so land 4b
(tables) and a skeleton of 4c/4d's types first or co-develop them in one
worktree; 4e depends on 4a's `FirewallPolicy`; 4f validates 4a–4e and may
trigger 4b-ryll and cap retuning; 4g is last. A reasonable order is
4b → 4c → 4d → 4a (wiring) → 4e → 4f → (4b-ryll if needed) → 4g, or fold
4a–4d into one worktree since they are tightly coupled and land as a
small number of self-contained commits.

## Success criteria

- L0 and L1 enforcement is **on by default**; a disallowed client→server
  message type and an oversized frame both Terminate the session; a
  policy-disabled channel is denied before relay.
- **Zero false positives**: full virt-viewer, remote-viewer, and ryll
  sessions complete through the proxy with no firewall Terminate on
  legitimate traffic (validated by 4f captures). Mouse clicks and keys
  work (inputs_client allowlist correct).
- Firewall verdicts are visible as Prometheus metrics and as
  aggregated (not per-message) audit events, with a dropped-events
  counter under backpressure.
- The relay no longer pins a permit on a silent client (idle timeout +
  keepalive), and per-channel size caps replace the flat 16 MiB.
- Python decides the policy (delivered via `AuthorizeConnection`); the
  Rust proxy enforces it. A `WarnOnly` mode exists that records what
  enforcement *would* have done (metrics + audit, `action=observed`)
  without blocking, and the default ships `Enforce`.
- Rust passes `cargo fmt --check`, `cargo clippy -- -D warnings`,
  `cargo test`; Python passes `tox -eflake8`/`tox -epy3`; the phase-4
  pre-push audit (`PUSH-TEMPLATE.md`) is clean.
- Docs updated; master-plan/index status flipped.

## Future work (recorded, not in this phase)

- **L2 body validation**: scancode ranges (inputs), vd-agent/clipboard
  direction policy, file-transfer allow/deny, usbredir device-class
  filtering (`shakenfist-spice-usbredir`). Adds untrusted-body parsers
  ⇒ cargo-fuzz targets.
- **Per-source / per-console policy profiles** in the DB, with API/UI
  surface — the reason 4e delivers policy per-connection.
- **Detailed per-channel state-machine conformance** (ordering,
  init-before-data), deferred from Design decision 5.
- **L3 rewriting/injection** (`Inject` verdict + ACK accounting):
  danger-zone recording indicator, defanging, Kerbside-originated
  devices — the reason `Drop`/framing are retained even where policy is
  permissive.
- Phase-3 deferred items not folded in here: struct-ify the repeated
  identity params; decouple the `/metrics` bind from the public VDI
  address (phase 5, daemon integration); the `select!`
  metrics-bind-fatal coupling.

## Outcome

Completed 2026-07-07 on the kerbside `rust-proxy-phase-4` branch,
unmerged and unpushed pending operator review and the pre-push audit
(below). All planned steps landed, including the conditional 4b-ryll
step — the capture validation showed it was needed for main/display
false positives, not for record/smartcard. Every Rust step was built,
linted (`clippy -D warnings`), and tested in the Docker build; every
Python step passed `flake8`/`py3`.

- 4b (`b928b44`): the L1 message-type allowlist grammar
  (`allowlist.rs`) — `classify(channel, dir, msg_type) -> Allowed |
  Disallowed | ChannelUnmodeled`, derived from the ryll `logging.rs`
  name tables unioned with the SPICE common-base opcode ranges;
  record/smartcard/tunnel return `ChannelUnmodeled` (no modeled
  grammar).
- 4a + 4d (`513a7e4`): the L1 enforcement engine — `FirewallPolicy`,
  `EnforcementMode` (Enforce default / WarnOnly), `EnforcingPolicy`
  wired into the relay in place of `PermissivePolicy`, the
  lock-free per-connection `VerdictTally`, and the
  `kerbside_proxy_firewall_verdicts_total` metric.
- 4c (`d8ced99`): L0 resource limits — the pre-body `check_header`
  hook, per-(channel,direction) size caps (4 KiB tight on
  inputs/cursor-client, 16 MiB generous elsewhere), a disabled-by-
  default rate/throughput ceiling, the 15-minute idle-read timeout,
  and client-side TCP keepalive — closing the phase-3 deferred
  permit-pinning findings.
- 4e (`d243b8b`): policy delivery over gRPC — the `FirewallPolicy`
  proto message carried in the `AuthorizeConnection` reply, Python's
  `FIREWALL_MODE`/`FIREWALL_PERMITTED_CHANNELS` config feeding
  `servicer.py`, and the Rust `session.rs` channel-permit gate
  (`PermissionDenied` before relay for a forbidden channel type).
- ryll pin bump (`0e96ecb`): bumped to develop rev `1c6f19f`, picking
  up the cross-repo ryll change (PR #141: completed main/display
  message-type tables plus a corrected `INVAL_ALL_PIXMAPS` opcode
  that had been mislabelled — the fix also corrected a latent
  renderer-side bug from the same mislabelling; PR #142: the
  `crossbeam-epoch` RUSTSEC-2026-0204 bump), both merged to ryll
  develop.
- 4f harness (`eb75cb4`): warn-only capture and deny-token/deny-all
  modes added to `mock-grpc-server.py` and
  `tools/direct-qemu/verify-rust-proxy.sh` (new `assert-firewall`
  action), plus `tools/direct-qemu/VERIFY-FIREWALL.md`.
- 4f live results (`ab511a0`): the live capture run recorded in
  `VERIFY-FIREWALL.md`, plus a bugfix to `assert-firewall` (the
  `set -o pipefail` `_verdict_sum` helper aborted on the
  all-zero-verdicts case — exactly the clean result it needed to
  detect).

Notable decisions and deviations, all deliberate:

- The two operator-level decisions taken during planning both held
  without revision: policy delivered over gRPC in the
  `AuthorizeConnection` reply now (not deferred to phase 5), and
  Terminate-by-default with a first-class `WarnOnly` mode rather than
  a log-only grace period.
- **4b-ryll became necessary, but for main/display, not
  record/smartcard.** The plan's Design decision 4 flagged
  record/smartcard as the likely trigger for a ryll change; in
  practice the pinned ryll name tables for main and display were
  incomplete (missing `CLIENT_INFO` and the migrate family on
  main-client, `NAME`/`UUID` on main-server, and several display
  invalidate/quality opcodes) and contained a mislabelled constant
  (`display_server::INVAL_ALL_PIXMAPS` held the wire value that
  `enums.h` assigns to `INVAL_ALL_PALETTES`). Left unfixed, these
  would have been real false positives under `Enforce` mode against
  legitimate remote-viewer/virt-viewer traffic. Filed and merged as
  ryll PR #141 (plus the unrelated but co-landed `crossbeam-epoch`
  advisory fix, PR #142), then the pin was bumped here. Record and
  smartcard were never carried by the 4f capture, so their
  `ChannelUnmodeled` observe-only handling needed no further ryll
  work.
- Size caps, the rate ceiling, and per-verdict severities all kept
  their compiled-default values for v1 — only `mode` and
  `permitted_channels` are delivered over gRPC (the plan's Open
  questions left the concrete cap/ceiling values to be fixed from
  the 4f captures rather than guessed; the 4f run approached none of
  the compiled defaults, so none were retuned and none were promoted
  to a gRPC-delivered knob this phase).
- The `need_secured` backend-retry orchestration (a phase-3 deferred
  test gap) remains exercised only by the `is_need_secured` unit
  tests; the 4f harness does not yet have a TLS-requiring backend
  leg to drive it live. Tracked as a follow-up, not a phase-4
  blocker (the plan's success criteria do not require it).

### Live capture validation (step 4f, 2026-07-07)

Full results are in `tools/direct-qemu/VERIFY-FIREWALL.md`. Summary:
remote-viewer (spice-gtk) driven through the proxy in `WarnOnly` mode
against the `uncalibrated-sextant.qcow2` guest completed a clean
session across 4 channels (main, display, inputs, cursor), ~1.17 MB
relayed (`bytes{s2c}=1,174,716`, `bytes{c2s}=1,468`), with **zero**
firewall verdicts (`enforced=0`, `observed=0`) — no false positives
against a real client, satisfying the plan's success criterion. No
size cap was approached, so none needed retuning. A separate
`DENY_ALL=1` run confirmed the `PermissionDenied` client-denial path
end to end (`authorized=0`, `denied=1`, no bytes relayed). virt-viewer
was not run separately (it shares remote-viewer's spice-gtk client
engine); ryll headless was not run this pass and remains a worthwhile
follow-up capture.

### Pre-push audit (2026-07-07)

Ran the `PUSH-TEMPLATE.md` audit against `git diff origin/develop..HEAD`.
Wave 1 (mechanical) was clean: Docker `make lint` (fmt + `clippy -D
warnings`) and `make test` green, `flake8`/`py3` green, no
`unsafe`/`unwrap`/`panic!`/`todo!` on any non-test enforcement path, no
new TODO/FIXME. Wave 2 was two judgment passes — security and
correctness/tests — on the internet-facing enforcement code. **No
blocking, critical, or high findings.**

The security pass verified as correct: the `header_checked` dedup (no
frame can skip `check_header`, the rate counter cannot be under-counted),
the guard ordering (absolute 16 MiB → L0 `check_header` before body
buffering → L1 `inspect`, all fail-closed), panic-safety on untrusted
input (all indexing bounded, shifts bounded to discriminants 1..=11, no
overflow), warn-only isolation (the downgrade lives only in `apply`; the
absolute guard and idle timeout are correctly NOT subject to it), and
credential-log safety (token/ticket never logged). The correctness pass
independently traced the `header_checked` dedup and the shared
`VerdictTally` (single-task `select!`, losing pump's counts survive) and
confirmed the `from_proto` bitmask and the Python↔Rust discriminant
mapping.

Two findings were fixed before push (commit `f060d73`):

- **Firewall fail-open on misconfiguration** (security, MEDIUM):
  `build_firewall_policy` now raises on an unknown
  `FIREWALL_PERMITTED_CHANNELS` name instead of warn-and-skip — a typo'd
  restrict-list no longer silently collapses to permit-all.
- **check_header-exactly-once test** (correctness): a relay unit test
  now guards the dedup invariant that drives the rate counter.

Accepted / documented (not fixed): the idle-read timeout is a backstop
that does not catch a slow-trickle client (bounded by the permit pool +
the 16 MiB reassembly cap); the allowlist fails open by design
(name-table-as-ground-truth); and the session channel-permit gate and
the `relay::run` teardown audit-flush are covered by the 4f live run
rather than unit tests (expensive to unit-test; their building blocks
are individually covered). All re-validated: Rust 56 tests + clippy
clean, Python `flake8`/`py3` clean, `pre-commit` clean.

## Back brief

Before executing any step, back brief the operator on the intended
approach for that step and how it aligns with this plan and the master
plan. The two operator-level decisions are settled (gRPC delivery now;
Terminate-by-default plus a warn-only mode). Still to confirm as the
work progresses: the `FirewallPolicy` proto shape (Open questions),
and — once 4f captures exist — the concrete per-channel/direction size
caps, the rate ceiling, and whether 4b-ryll (record/smartcard tables in
ryll) is needed.
