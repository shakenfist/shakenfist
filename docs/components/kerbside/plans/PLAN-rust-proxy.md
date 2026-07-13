# Rust SPICE proxy (kerbside-proxy)

## Prompt

Before responding to questions or discussion points in this
document, explore the kerbside codebase thoroughly. Read
relevant source files, understand existing patterns (the
SPICE protocol implementation in `kerbside/spiceprotocol/`,
the proxy connection model in `kerbside/proxy.py`, the
source driver abstraction in `kerbside/sources/`, the REST
API in `kerbside/api.py`, the SQLAlchemy/alembic data model
in `kerbside/db.py` and `alembic/`, Pydantic-based config in
`kerbside/config.py`, audit logging, and the .vv file
generation path). Ground your answers in what the code
actually does today. Do not speculate about the codebase
when you could read it instead. Where a question touches on
external concepts (SPICE protocol, QXL, vdagent, libvirt
graphics, OpenStack Nova consoles, oVirt console API,
Shaken Fist), research as needed to give a confident
answer. Flag any uncertainty explicitly rather than
guessing.

This plan crosses repository boundaries. Phases that land
in other repos must consult those repos in the same
grounded way:

- `shakenfist/ryll` — the Rust SPICE client. The
  server-side protocol primitives land in its
  `shakenfist-spice-protocol` crate. Explore
  `shakenfist-spice-protocol/src/{link,messages,client,constants}.rs`
  before changing anything: the client-side halves of the
  handshake already exist and the new server-side halves
  must round-trip against them.
- `shakenfist/shakenfist` — the Shaken Fist cloud. Its
  `protos/` directory and the database daemon
  (`shakenfist/daemons/database/main.py`) are the local
  precedent for gRPC and the protoc toolchain. Do NOT
  copy the unframed protobuf-over-UDS pattern used by
  privexec/nodelock; that pattern is tracked as technical
  debt in shakenfist/shakenfist#3315.
- `shakenfist/kerbside-patches` — patches applied to
  upstream components for full Kerbside integration;
  relevant when deployment packaging (Kolla) changes.
- Reference checkouts at `/srv/src-reference/spice/` —
  the canonical protocol headers
  (`spice-protocol/spice/protocol.h`, `enums.h`), the
  server-side handshake state machine
  (`spice/server/reds.cpp`), and the client side
  (`spice-gtk/src/spice-channel.c`). Use these to verify
  wire-format questions rather than guessing.
- External: `astral-sh/uv` as the packaging precedent
  (maturin `bindings = "bin"` plus a python shim that
  locates the installed binary).

All planning documents for this plan — master and every
phase plan — live in this repo's `docs/plans/`, regardless
of which repo each phase's *implementation* lands in.
Kerbside is the driver of this work; keeping every phase
plan co-located keeps the design conversation in one
searchable place. Sub-agents executing a cross-repo phase
must therefore be briefed that the plan they are following
lives in `shakenfist/kerbside/docs/plans/` even when all
of their commits will land elsewhere.

Consult `ARCHITECTURE.md` for the overall proxy
architecture, channel model, and connection lifecycle.
Consult `AGENTS.md` for build commands, project
conventions, key file map, and code organisation. The
`docs/` tree contains protocol documentation derived from
upstream SPICE sources and verified against this
implementation — `protocol-overview.md`,
`channel-protocols.md`, `compression-protocols.md`,
`spice-link-protocol.md`, `vd-agent-protocol.md`,
`scancodes.md`, `usb-redirection.md`,
`console-sources.md`, `proxy-architecture.md`, and
`capabilities.md`. `docs/proxy-architecture.md` maps
almost 1:1 onto `kerbside/proxy.py` and is the primary
companion reference for the port.

When we get to detailed planning, I prefer a separate plan
file per detailed phase. These separate files should be
named for the master plan, in the same directory as the
master plan, and simply have `-phase-NN-descriptive`
appended before the `.md` file extension. Tracking of
these sub-phases should be done via a table in this master
plan under the Execution section.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

Kerbside's SPICE proxy is currently written in Python
(`kerbside/proxy.py` plus `kerbside/spiceprotocol/`). It
works, but has structural costs:

- **Process-per-channel.** Every SPICE channel of every
  session is a full OS process (`proxy.py` spawns a
  `multiprocessing.Process` per accepted connection). A
  single virt-viewer session produces five to seven
  processes. Worker lifecycle is managed by a psutil
  sweep against the `proxychannels` DB table.
- **Python on the hot path.** Even with traffic
  inspection disabled, the relay loop frames every
  message via `struct.unpack_from` in Python before
  forwarding.
- **Byte-shovelling after auth.** Once a token is
  validated, the proxy relays traffic opaquely to a qemu
  SPICE server — software whose own upstream
  documentation says should never face the internet, and
  whose demarshalling of client messages has a history of
  memory-safety CVEs. Kerbside closes the pre-auth gap
  but does nothing post-auth.
- **The disabled danger-zone machinery.** The only
  feature that ever modified traffic — the display-channel
  "danger zone" border indicating that a session was
  being recorded — is disabled due to hypervisor OOMs and
  other bugs, leaving the inserted-packet/ignore-ack
  accounting it required unused. The *goal* behind it is
  not abandoned: session recording is a desired future
  capability (see Mission). It is the buggy Python
  implementation of the indicator that we are dropping,
  not the ambition.

Exploration findings that shape this plan (verified
2026-07-04 against the current trees):

- The proxy is already a separate OS process whose *only*
  coupling to the rest of Kerbside is the shared MariaDB
  database plus an in-memory metrics queue. Its entire
  interaction surface is six operations:
  `get_token_by_token`, `get_source`, `get_console`,
  `record_channel_info`, `add_audit_event`, and
  `remove_proxy_channel` / `remove_node_channels`. All of
  them happen at connection setup, none on the data path.
- Ryll's `shakenfist-spice-protocol` crate was split out
  explicitly so a Kerbside rewrite could reuse it. The
  backend (hypervisor-facing) leg of a proxy is nearly
  turnkey: `SpiceClient::connect_channel` already does
  TCP/TLS with custom CA, the client-side link handshake,
  and OAEP ticket encryption. The client-facing leg does
  not exist anywhere in Rust: parsing an inbound
  `SpiceLinkMess`, serialising a `SpiceLinkReply`, RSA
  keypair generation and ticket *decryption*, a server
  TLS acceptor, and a server-side `SpiceStream` variant
  are all new work.
- Wire-compat details the Rust proxy must reproduce
  exactly: per-connection 1024-bit RSA keypair,
  OAEP/SHA-1 ticket encryption, hardcoded capability
  words toward the client (common=11, channel=9, because
  the backend is not known at handshake time), 6-byte
  mini-header framing only, the insecure-port
  `need_secured` redirect, and the `need_secured` retry
  when connecting to the hypervisor.
- The shakenfist privexec/nodelock protobuf-over-UDS
  pattern has no message framing and is not portable to a
  Rust peer; it was judged technical debt
  (shakenfist/shakenfist#3315). Shaken Fist's database
  daemon already uses real gRPC, so gRPC-over-UDS
  converges rather than diverges the ecosystem.
- There are no production deployments of Kerbside yet, so
  transitions do not need to be graceful and the firewall
  can ship enforcing from day one.

## Mission and problem statement

Split Kerbside into two cooperating components:

- **kerbside (Python, unchanged repo layout)** — owns the
  database, REST API, web UI, console source scraping,
  token lifecycle, audit storage, and *policy decisions*.
- **kerbside-proxy (Rust, new, in this repo)** — accepts
  incoming SPICE connections, terminates client TLS,
  performs the SPICE link handshake, consults the Python
  side over gRPC to authorise the connection and learn
  the target, connects to the hypervisor, and relays
  traffic through an inspection-first pipeline that
  *enforces* firewall policy.

The long-term goal is for Kerbside to become an
application firewall for SPICE, not merely a relay. Rust
is chosen for performance, for memory safety in the
component that parses untrusted internet-facing bytes,
and for the headroom to do deeper packet inspection over
time. Python decides policy; Rust enforces it.

A second long-term capability sits behind the same
architecture: **active participation in the traffic**,
not just filtering it. The clearest example is session
recording — in highly regulated environments (and for
testing and debugging) the proxy should eventually be
able to record sessions as they are executed, including
signalling to the user that recording is in progress (the
"danger zone" indicator the Python proxy attempted).
Other envisaged features in this class include defanging
unsafe traffic by rewriting rather than dropping it,
Kerbside-provided virtual devices (for example a
Kerbside-managed USB disk offered to the guest over
usbredir, making the proxy the *originator* of channel
traffic), and an LLM/MCP sidecar riding along a session.
None of these are v1 features, but no v1 implementation
choice may make them impossible later. Concretely, the
relay pipeline must retain per-message framing
everywhere, and its abstractions must leave room for
passive taps (copying framed traffic to a recorder sink),
message rewriting, and message origination with
flow-control-aware ACK accounting — designed for now,
implemented later.

### Design decisions (settled)

1. **IPC: tonic/gRPC over a unix domain socket.** The
   Python daemon binds the UDS and hosts the gRPC server;
   the Rust proxy is the client. gRPC provides framing,
   request/response correlation, deadlines, and
   server-streaming push (used for session termination
   and policy updates). The RPC surface is approximately:
   `AuthorizeConnection` (token plaintext in, denial or
   full target descriptor plus firewall policy out),
   `RegisterChannel` / `DeregisterChannel`, `AuditEvent`
   (batched), and a server-streaming `ProxyControl` for
   termination/policy pushes.
2. **Inspection-first relay.** Every message is framed
   (6-byte mini-header) and passed through a policy hook
   with `Forward | Drop | Terminate` verdicts (an
   `Inject` verdict is designed for but not implemented
   in v1). Permissive pass-through is a policy, not a
   separate fast path. The dangerous direction
   (client→qemu) is also the low-bandwidth direction, so
   strict parsing there is cheap; the bulk direction
   (server→client display data) stays framed-but-opaque.
   The pipeline design must also leave room for the
   active-participation features described in the Mission
   — passive per-message taps (session recording),
   message rewriting (defanging), and message origination
   (Kerbside-provided devices) — which is one of the
   reasons pass-through-without-framing is not an
   acceptable architecture even where policy is
   permissive.
3. **Enforcement on from day one.** L0 (framing sanity:
   header validity, per-channel size caps, buffer and
   rate limits) and L1 (message-type allowlists per
   channel and direction, unknown/obsolete channel
   rejection, basic state-machine conformance) both ship
   enforcing. No log-only grace period; there are no
   production deployments to disrupt.
4. **Server-side SPICE primitives land in
   `shakenfist-spice-protocol`** (ryll repo), whose
   stated scope already includes servers and proxies:
   `SpiceLinkMess::parse`, `SpiceLinkReply::serialize`,
   RSA keypair generation, `decrypt_password`, a
   server-capable `SpiceStream`, and reconciliation of
   the rustls crypto-provider inconsistency (the crate
   verifier uses aws-lc-rs while ryll installs ring).
   These parsers face untrusted input and get cargo-fuzz
   targets from the start.
5. **Code home and packaging.** The proxy crate lives in
   this repo (`rust/kerbside-proxy/`). It is published as
   a separate `kerbside-proxy` PyPI package built with
   maturin `bindings = "bin"` (the uv pattern: the binary
   lands in the wheel's scripts directory; a small Python
   shim provides `find_proxy_bin()`). The pure-python
   `kerbside` package stays on setuptools and gains an
   exact-version-pinned dependency on `kerbside-proxy`,
   so `pip install kerbside` still yields a working proxy
   and the gRPC contract on both ends matches by
   construction.
6. **Process model.** The Python daemon exec()s the Rust
   proxy as a supervised child: binds the gRPC UDS first,
   passes bootstrap configuration (UDS path, bind
   address/ports, cert paths, Prometheus port, log level)
   as CLI flags derived from the pydantic config,
   forwards SIGTERM with a deadline, and keeps the
   current exit-non-zero-on-child-death behaviour. The
   proxy inherits stderr so `tracing` output lands in the
   existing log stream.
7. **Concurrency and observability.** Tokio task per
   accepted connection replaces process-per-channel. The
   multiprocessing metrics queue is replaced by a
   Rust-native Prometheus `/metrics` endpoint. Firewall
   verdicts are aggregated/rate-limited locally before
   being reported as audit events. The proxy is
   instrumented with the `tracing` crate from day one —
   structured spans around the connection lifecycle
   (accept, TLS, handshake, authorize RPC, backend
   connect, relay) — and the gRPC contract carries a
   per-connection correlation id that flows into audit
   events. Full OpenTelemetry (OTLP export, Python-side
   instrumentation, cross-boundary trace propagation) is
   deliberately deferred to a later master plan; the
   spans and correlation ids added here are the hooks
   that make attaching it cheap.
8. **Out of scope for v1** (absent or disabled in the
   Python proxy, so parity does not require them): SASL
   authentication, the full 18-byte non-mini data header,
   multi-word capability parsing, session recording, and
   the danger-zone recording indicator (L3
   injection/rewriting is future work, but the pipeline
   must not preclude it — see decision 2).

## Open questions

- Exact `.proto` message shapes, field numbering, and
  error taxonomy for the kerbside-api service; whether
  `AuthorizeConnection` returns the source CA cert inline
  or by reference. To be settled in phase 2 planning.
- Where firewall policy lives in v1: a single deployment
  -wide policy expressed in pydantic config and echoed
  through `AuthorizeConnection`, versus per-source or
  per-console policy columns in the database (implying an
  alembic migration and API/UI surface). Current lean:
  deployment-wide in v1, per-console as future work.
- Audit event batching semantics: flush interval, local
  buffer bounds, and what is dropped (with a counter)
  under audit backpressure.
- Whether the plaintext listener remains a full listener
  in Rust or is reduced to a minimal `need_secured`
  responder; and whether `PUBLIC_INSECURE_PORT` remains
  in `.vv` files.
- Wheel matrix scope for the first release: manylinux
  x86_64 only, or x86_64 + aarch64. Sdist fallback
  requires a Rust toolchain at install time and must be
  documented either way.
- Release mechanics for lockstep publishing of two PyPI
  packages from one repo with setuptools_scm on one side
  and maturin on the other.
- The fate of the `TRAFFIC_INSPECTION` /
  `TRAFFIC_INSPECTION_INTIMATE` / `TRAFFIC_OUTPUT_PATH`
  config options: reimplement as a tracing mode in the
  Rust proxy, or retire.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Server-side SPICE primitives in shakenfist-spice-protocol (ryll repo) | PLAN-rust-proxy-phase-01-server-primitives.md | Complete (merged to ryll develop, PR #138) |
| 2. gRPC contract and Python UDS server in the kerbside daemon | PLAN-rust-proxy-phase-02-grpc-contract.md | Complete (kerbside branch rust-proxy, unmerged) |
| 3. Proxy crate skeleton: listeners, handshake, backend leg, relay | PLAN-rust-proxy-phase-03-proxy-skeleton.md | Complete (kerbside branch rust-proxy-phase-3, unmerged) |
| 4. Firewall policy engine: L0 + L1 enforcing | PLAN-rust-proxy-phase-04-firewall.md | Complete (kerbside branch rust-proxy-phase-4, unmerged) |
| 5. Daemon integration: exec/supervision, termination push | PLAN-rust-proxy-phase-05-daemon-integration.md | Complete (kerbside branch rust-proxy-phase-5, unmerged) |
| 6. Packaging: maturin wheel, CI build matrix, version pinning | PLAN-rust-proxy-phase-06-packaging.md | Complete (kerbside branch rust-proxy-phase-6, unmerged) |
| 7. CI: direct-qemu lane on the Rust proxy, loadtest comparison | PLAN-rust-proxy-phase-07-ci.md | Complete (kerbside branch rust-proxy-phase-7, unmerged) |
| 8. Cutover: Rust as default, retire the Python proxy, docs | PLAN-rust-proxy-phase-08-cutover.md | Complete (kerbside branch rust-proxy-phase-8, unmerged) |

Phase sketches (each to be expanded into its own plan file
before execution):

1. **Server primitives (ryll repo).** Add
   `SpiceLinkMess::parse`, `SpiceLinkReply::serialize`,
   RSA keypair generation, `decrypt_password`, and a
   server-capable `SpiceStream` to
   `shakenfist-spice-protocol`. Round-trip tests against
   the existing client-side implementations; cargo-fuzz
   targets for every new parser; reconcile the
   ring/aws-lc-rs provider split. Release a crate version
   the proxy can pin.
2. **gRPC contract.** `protos/` in this repo defining the
   kerbside-api service; generated Python (grpcio) and
   Rust (tonic/prost) code with a documented codegen
   step; the UDS server hosted in the Python daemon
   process sharing `db.py`; unit tests exercising every
   RPC in-process. Socket directory permissions
   deliberately restrictive.
3. **Proxy skeleton.** `rust/kerbside-proxy/` binary
   crate: dual listeners with `need_secured` redirect on
   the plaintext port; TLS termination; client-facing
   handshake using phase 1 primitives; `AuthorizeConnection`
   round trip; backend leg via `SpiceClient` including
   the `need_secured` retry and host_subject
   verification; framed relay with a no-op policy;
   Prometheus endpoint; CLI bootstrap flags. Verified
   end-to-end against a real qemu with both virt-viewer
   and ryll headless as clients. A GitHub Actions
   workflow running fmt/clippy/test and building the
   fuzz targets lands in this phase alongside the crate
   and runs on every PR from then on.
4. **Firewall.** The policy engine and verdict pipeline;
   L0 limits and L1 per-channel/direction allowlists
   derived from `shakenfist-spice-protocol` constants;
   policy delivered in the `AuthorizeConnection` reply;
   audit aggregation; metrics for verdicts. Enforcing by
   default, with the allowlists validated against
   virt-viewer, remote-viewer, and ryll traffic captures.
5. **Daemon integration.** `find_proxy_bin()`, subprocess
   supervision replacing the `multiprocessing.Process`
   spawn, config selection between the Python and Rust
   proxies during the transition, SIGTERM forwarding, and
   the `ProxyControl` stream wired to API-driven session
   termination so in-flight connections are killed.
6. **Packaging.** The maturin project for
   `kerbside-proxy`, the Python shim package, the wheel
   build matrix in CI, the exact-pin dependency from
   `kerbside`, and lockstep release automation.
7. **CI.** The direct-qemu lane runs against the Rust
   proxy (initially as an additional lane alongside the
   Python one); the latency loadtest compares the two
   proxies to quantify the performance claim.
8. **Cutover.** Rust becomes the default and only proxy;
   the Python proxy path and its dead machinery are
   removed; `ARCHITECTURE.md`, `AGENTS.md`, `README.md`,
   `docs/proxy-architecture.md`, and
   `docs/configuration.md` are rewritten to describe the
   split; kerbside-patches reviewed for Kolla deployment
   impact.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The management session (this
conversation) is reserved for planning, review, and
decision-making. This keeps the management context lean
and avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with
   the brief from the plan, at the recommended effort
   level and model.
3. **Review** the sub-agent's output in the management
   session. Check the actual files — the sub-agent's
   summary describes what it intended, not necessarily
   what it did.
4. **Fix or retry** if the output is wrong. Diagnose
   whether the brief was insufficient (improve it) or the
   model was too light (upgrade it), then re-run.
5. **Commit** once the management session is satisfied
   with the result.

This applies to all steps, including high-effort ones. If
a sub-agent can't succeed even with a detailed brief and
the right model, that's a signal the brief needs
improving, not that the management session should do the
implementation itself.

Use `isolation: "worktree"` for sub-agents when the change
is risky or experimental. The worktree is discarded if the
output is unsatisfactory. For safe, well-understood
changes, sub-agents can work directly in the main tree.

### Planning effort

The master plan itself should always be created at **high
effort** — it requires broad codebase understanding,
cross-referencing multiple source files, and making
judgment calls about scope and sequencing.

Each phase plan should specify the recommended effort
level for planning that phase. Phases involving deep
protocol research (SPICE handshake semantics, wire-format
compatibility, firewall allowlist derivation), the gRPC
contract design, or architectural decisions should be
planned at high effort. Phases that are mechanical or
follow well-established patterns (packaging, CI wiring)
can be planned at medium effort.

### Step-level guidance

Each phase plan should include a table like this:

```
| Step | Effort | Model  | Isolation | Brief for sub-agent |
|------|--------|--------|-----------|---------------------|
| 1a   | medium | sonnet | none      | One-sentence summary of what to do and which files to touch |
| 1b   | high   | opus   | worktree  | Why this needs high effort: requires understanding X to do Y |
```

**Effort levels:**
- **high** — Requires reading multiple files, making
  judgment calls, understanding non-obvious invariants
  (SPICE channel ordering, capability negotiation, ticket
  / token lifecycle, TLS termination/origination
  differences, audit log guarantees), or researching
  external references. The sub-agent needs to think
  carefully about edge cases.
- **medium** — The plan provides enough context that the
  sub-agent can follow a clear brief. May need to read a
  few files but the approach is well-defined.
- **low** — Purely mechanical changes (rename, reformat,
  add a log line). The brief is a complete instruction.

**Model choice:** The planner should recommend which
model is best suited for each step. This is a judgment
call, not a rigid rule — the right model depends on what
the step requires, not on whether it's "planning" or
"implementation".

- **opus** — Best for steps that require deep reasoning,
  cross-file architectural understanding, subtle
  correctness judgment, SPICE protocol research, gRPC
  contract design, or intricate implementation where
  getting it wrong would be costly to debug (silent
  protocol bugs, token leaks, audit gaps, firewall
  bypasses).
- **sonnet** — Good default for well-briefed
  implementation work. Faster and cheaper than opus.
  Works well when the plan front-loads the research and
  the brief is detailed enough that the agent doesn't
  need to make broad judgment calls.
- **haiku** — Suitable for purely mechanical tasks:
  search-and-replace, adding log lines, running
  commands. The brief must be a near-complete
  instruction.

The model choice interacts with effort level and brief
quality. A detailed brief compensates for a lighter model
— sonnet at medium effort with a thorough brief often
matches opus at medium effort with a vague brief. The
planner's job is to write briefs good enough that the
recommended model can succeed.

Note: the model also determines the context window (opus
has 1M tokens, sonnet and haiku have 200K). Steps that
require holding many files in context simultaneously may
need opus for that reason alone, even if the reasoning
itself is straightforward.

**When in doubt, skew to the more capable model.** Saving
money only matters if the outcome is still acceptable. A
failed or low-quality implementation wastes more time
(and therefore more money) than using a heavier model
would have cost. Only recommend a lighter model when you
are confident the brief is detailed enough for it to
succeed.

**Brief for sub-agent:** This is the key field. Write it
as if briefing a colleague who has never seen the
codebase. Include: what to change, which files to touch,
what patterns to follow, and any non-obvious constraints.
The better the brief, the lower the effort level needed
and the lighter the model that can succeed.

A good brief front-loads the research the planner
already did, so the implementing agent doesn't repeat it.
For example, instead of "add the auth RPC", write "add an
`AuthorizeConnection` handler to the gRPC servicer in
`kerbside/rpc.py`, following the token lookup sequence in
`kerbside/proxy.py` `ClientPassword` (token via
`db.get_token_by_token`, then `db.get_source`, then
`db.get_console`), returning the denial reason taxonomy
defined in `protos/kerbside.proto`, and emitting the same
audit events the Python proxy emits today."

### Management session review checklist

After a sub-agent completes, the management session
should verify:

- [ ] The files that were supposed to change actually
      changed (read them, don't trust the summary).
- [ ] No unrelated files were modified.
- [ ] Python changes pass `tox -eflake8` and `tox -epy3`.
- [ ] Rust changes pass `cargo fmt --check`,
      `cargo clippy`, and `cargo test` in the affected
      workspace; new parsers have fuzz targets and the
      fuzz targets at least build.
- [ ] Wire-format changes are cross-checked against the
      Python implementation and/or the reference sources
      under `/srv/src-reference/spice/`.
- [ ] The changes match the intent of the brief — not
      just syntactically correct but semantically right.
- [ ] Commit message follows project conventions
      (including the `Co-Authored-By` line with model,
      context window, effort level, and other settings).

## Administration and logistics

### Success criteria

We will know when this plan has been successfully
implemented because the following statements will be true:

* `pip install kerbside` on a supported platform installs
  a working system in which the Rust proxy carries all
  SPICE traffic; deployers run the same commands they run
  today.
* virt-viewer/remote-viewer and ryll (headless) both
  complete full sessions through the Rust proxy against a
  real qemu, including reconnection and token-expiry
  behaviour matching the Python proxy.
* The direct-qemu CI lane passes against the Rust proxy,
  and the latency loadtest demonstrates equal-or-better
  performance versus the Python proxy.
* L0 and L1 firewall enforcement is on by default, with
  verdicts visible in audit events and Prometheus
  metrics, and produces no false positives against the
  supported clients.
* Session termination via the API disconnects in-flight
  Rust proxy connections promptly.
* The Python code passes `tox -eflake8` and `tox -epy3`;
  the Rust code passes `cargo fmt --check`, `cargo
  clippy` without warnings, and `cargo test`; parsers of
  untrusted input have cargo-fuzz targets.
* The Rust proxy is exercised by GitHub Actions CI on
  every pull request — `cargo fmt --check`, clippy,
  `cargo test`, a build of the fuzz targets, and the
  wheel build all run as CI jobs, not merely as local
  Makefile targets — in addition to the direct-qemu
  integration lane running against the Rust proxy.
* Python strings use single quotes except docstrings;
  Python lines wrap at 120 characters; trailing
  whitespace is removed. Rust code is rustfmt-formatted.
* `README.md`, `ARCHITECTURE.md`, and `AGENTS.md` have
  been updated to describe the split, and
  `docs/proxy-architecture.md`, `docs/configuration.md`,
  and related protocol documentation match the shipped
  implementation.
* The relevant patches in `shakenfist/kerbside-patches`
  have been reviewed and updated for the new process and
  packaging model.

### Future work

* **L2 body validation**: parse and validate selected
  client→server message bodies — inputs scancode ranges,
  vd-agent/clipboard direction policy, file-transfer
  allow/deny, usbredir device-class filtering (the
  `shakenfist-spice-usbredir` crate exists).
* **Session recording**: record sessions as they execute,
  for highly regulated environments and for
  testing/debugging. Builds on the passive per-message
  tap the v1 pipeline must already accommodate; needs a
  storage format, retention policy, and API surface.
* **L3 rewriting/injection**: reimplement the danger-zone
  recording indicator safely, with flow-control-aware
  insertion (`Inject` verdict and ACK accounting) — and
  diagnose the hypervisor OOM the Python implementation
  triggered. The same machinery underpins other
  active-participation features: defanging unsafe traffic
  by rewriting it, Kerbside-provided virtual devices
  (e.g. a Kerbside-managed USB disk offered over
  usbredir), and an LLM/MCP sidecar attached to a
  session.
* **Full OpenTelemetry support** as its own master plan:
  OTLP export from the Rust proxy (layering on the
  `tracing` spans this plan already requires),
  instrumentation of the Python API/daemon (Flask,
  SQLAlchemy, the gRPC servicer), trace-context
  propagation across the UDS boundary via the
  correlation ids in the gRPC contract, and sampling
  policy. Deferred because validating this plan's
  performance claim needs only the Prometheus metrics
  and the latency loadtest, and there is no collector
  infrastructure in any deployment yet.
* **Backend `host_subject` enforcement.** The Python proxy verified
  the hypervisor's TLS certificate subject against the console's
  `host_subject`. The ryll `SpiceClient` the Rust proxy reuses does
  not — its CA verifier validates the chain but relaxes hostname/
  subject matching (SPICE certs often lack SANs), so `host_subject`
  is currently informational. This is a security-parity regression
  accepted for the phase-3 skeleton. Restore real subject pinning
  before production — most likely by adding optional subject
  verification to the ryll crate's verifier (so ryll benefits too)
  and having the proxy pass the console's `host_subject`. Tracked
  here so it is not lost.
* **Per-source / per-console firewall policy profiles**
  in the database, with API and web UI surface.
* **SASL auth and the full non-mini data header**, if a
  client population ever requires them.
* **Wheel matrix growth**: aarch64, musllinux, and
  whatever platforms deployers actually ask for.
* **Bespoke SPICE servers** (speculative, out of scope
  for Kerbside): the server-side handshake primitives
  this plan adds to `shakenfist-spice-protocol` (phase 1)
  are exactly what a *native* SPICE server needs, not
  only a proxy. Once the crate can accept a client,
  present a key, and complete auth, it becomes possible
  to write standalone SPICE servers that render arbitrary
  content for any standard SPICE client (virt-viewer,
  remote-viewer, ryll) — for example rendering a
  dashboard directly to SPICE instead of a resource-heavy
  HTML5 web UI (a Home Assistant dashboard was the
  motivating daydream). This is a red herring for the
  proxy, but it is a reason to keep the phase-1 server
  primitives clean, well-documented, and independent of
  proxy-specific concerns so a future non-proxy consumer
  can reuse them. No work is planned here; recorded so the
  idea is not lost.
* shakenfist/shakenfist#3315 — migrate Shaken Fist's
  privexec/nodelock IPC off the unframed
  protobuf-over-UDS pattern (filed from this design
  work; not blocking Kerbside).
* Consider connection draining across proxy restarts so
  upgrades do not drop live VDI sessions.

### Bugs fixed during this work

None yet. When planning phase 1, scan the
shakenfist/kerbside and shakenfist/ryll issue trackers
for open bugs related to the proxy or the protocol crate
(e.g. the ryll-side "mouse clicks not working through
kerbside proxy" interop note) and either fold them into
the relevant phase or record them here as consciously
deferred.

### Documentation index maintenance

When creating a new master plan from this template,
update the following file in `docs/plans/`:

* **`index.md`** — add a row to the *Master plans* table
  with the creation date, a link to the plan, a one-line
  intent summary, the initial status, and links to each
  phase plan file. Keep the table in chronological
  order.

When all phases of a plan are complete, update the
status column in `index.md` to *Complete*.

### Back brief

Before executing any step of this plan, please back
brief the operator as to your understanding of the plan
and how the work you intend to do aligns with that plan.
