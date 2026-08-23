# Embrace TLS across Shaken Fist

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read
relevant source files, understand existing patterns (daemon
startup, gRPC server and channel construction, MariaDB
connection setup, the existing internal-CA role in the
deployer, certificate distribution flow), and ground your
answers in what the code actually does today. Do not
speculate about the codebase when you could read it instead.
Where a question touches on external concepts (TLS, mTLS,
gRPC credentials, MariaDB SSL, X.509 SANs, PKI lifecycle,
cert rotation patterns), research as needed to give a
confident answer. Flag any uncertainty explicitly rather
than guessing.

All planning documents should go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the system architecture
overview, daemon structure, and the gRPC channels currently
in use. Consult `CLAUDE.md` for build commands and project
conventions. Key references inside the repo include
`shakenfist/daemons/database/main.py` and the other gRPC
servers under `shakenfist/daemons/*/`,
`shakenfist/grpc_client_factory.py` (or equivalent client
construction code — confirm during phase 0 research),
`shakenfist/deploy/ansible/roles/pki_internal_ca/`
(the existing CA role that becomes a dev-only convenience),
and the SPICE-cert path which is the closest existing
example of cert distribution.

This plan is currently less formed than its sibling
(`PLAN-remove-primary.md`). The Open Questions section is
larger than usual and should be resolved into a Decisions
section before phase 1 begins. The phase breakdown below
is provisional and may be re-cut once phase 0 (research and
decisions) completes.

When we get to detailed planning, I prefer a separate plan
file per detailed phase. These separate files should be named
for the master plan, in the same directory as the master
plan, and simply have `-phase-NN-descriptive` appended before
the `.md` file extension.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

Shaken Fist's gRPC traffic between daemons is currently
plaintext on the cluster mesh network. The MariaDB
connection is plaintext. The internal API (sf-api on :13000,
sf-database on :13005, sf-eventlog on :13002, the
prometheus exporters, etc.) is plaintext on the mesh. The
deployer does ship a `pki_internal_ca` role and an internal
CA used today for **SPICE console certificates**, but that
CA's reach does not extend to the cluster's own RPC.

The mesh network is typically a private VLAN, so this is not
"the API is on the public internet in cleartext" bad. It is
nonetheless behind where SF should be as an adult cloud
platform. An operator with a security review will ask, and
the answer today is "the mesh is private; trust it." That's
not a great answer.

Once `PLAN-remove-primary.md` lands, operators will already
be expected to bring their own PKI — that plan establishes
the operator-provides-CA surface. This plan is then about
*actually using* that PKI: turning the gRPC channels, the
MariaDB connection, and the HTTP API endpoints into TLS
(and where it makes sense, mTLS) connections, and handling
the operational concerns that follow (cert rotation, expiry
monitoring, peer-identity verification, error paths during
rotation).

## Mission and problem statement

Every wire connection between Shaken Fist components, and
between Shaken Fist and its MariaDB, is TLS-protected.
Where the protocol can sensibly require peer-identity (gRPC
between daemons, especially), mTLS is enforced and the peer
certificate's SANs are validated against an expected set
(node identity / role).

Concretely:

- **gRPC between SF daemons** uses mTLS. Each daemon
  presents a cert identifying its host, and verifies its
  peer's cert against the cluster CA bundle and (where
  appropriate) an expected SAN.
- **The SF-to-MariaDB connection** uses TLS. The MariaDB
  server's cert is verified against the operator-provided
  CA. mTLS into MariaDB (client-cert auth instead of, or
  in addition to, password auth) is desirable but optional
  — many operator MariaDB deployments don't surface it
  cleanly.
- **The external REST API** (sf-api on :13000) supports
  TLS. Operators typically already terminate TLS at their
  own load balancer / reverse proxy, so this is more about
  *making it possible to terminate end-to-end* than about
  forcing it. Plaintext sf-api remains supported behind a
  trusted proxy.
- **Certificate rotation works without daemon restarts.**
  Every SF daemon reloads its TLS material on SIGHUP (or
  via a filesystem watcher — decide in phase 0). Cert
  expiry within some window emits an event log warning and
  a prometheus metric so operators can monitor.
- **The existing `pki_internal_ca` role becomes an opt-in
  convenience** for dev / test / evaluation deployments
  that don't have a real PKI handy. The production path is
  always "operator brings cert paths."

## Open questions

Many. This plan is intentionally less formed.

1. **mTLS scope: every-channel-every-direction, or selective?**
   The cleanest model is "every gRPC channel is mTLS, full
   stop." But that's a lot of churn at once. An alternative
   is to start with the channels that cross host boundaries
   (sf-database from anywhere, sf-eventlog from anywhere)
   and leave localhost-only channels (privexec) as plaintext.
   Cleaner long-term to do them all; more painful short-term.
2. **Cert rotation mechanism: SIGHUP or filesystem watcher?**
   SIGHUP is universal and predictable but requires the
   operator to remember to signal each daemon after writing
   new cert files. A watcher (inotify) is automatic but
   needs care around partial-write atomicity. Some
   ecosystems prefer "watch and reload"; others prefer
   "explicit signal." Decide in phase 0.
3. **Peer identity validation: SAN-based or just CA-trust?**
   The minimum is "the cert chains to our CA." Stronger is
   "the cert's SAN names this host in our inventory."
   Stronger still is "the cert's SAN encodes the daemon
   role." Pick a level and stick with it.
4. **MariaDB TLS in the BYO world.** Some operator
   MariaDBs (RDS, Aiven) make TLS trivial. Others (vanilla
   on-prem) make it fiddly. What's the SF default — require
   TLS, prefer TLS, or "you tell us"? The honest answer is
   probably "prefer TLS with verify, fall back to verify-only
   if operator opts down" — but it needs deciding.
5. **External API TLS termination.** The remove-primary plan
   already documents that operators put their own LB in
   front of sf-api. Should sf-api itself ever speak TLS, or
   is "plaintext behind a proxy" sufficient forever?
   Arguments both ways.
6. **Cert format and on-disk layout.** Where do cert files
   live, what are they named, who owns them, what mode bits?
   This sounds trivial but operators bake assumptions in
   their PKI distribution tooling; pick a layout and
   document it before phase 1 so it doesn't change later.
7. **What about the prometheus exporters, sf-api, the
   admin/sf-ctl gRPC channel?** Each is a separate TLS
   decision. Probably they fall out of the same answer as
   the gRPC mTLS question, but worth being explicit about
   the full inventory of wire connections during phase 0
   so we don't miss one.
8. **What does the test rig do?** mTLS-everywhere has a
   real impact on cluster_ci — every test deploy needs
   certs minted. The `pki_internal_ca` role provides this
   today for SPICE; extending it to cover the cluster
   itself is straightforward but worth designing alongside
   the production path so the dev / test path doesn't
   diverge.

## Execution

Provisional. To be re-cut after phase 0 produces a
decisions document.

| Phase | Plan | Status |
|-------|------|--------|
| 0. Research and decisions document | PLAN-embrace-tls-phase-00-decisions.md | Not started |
| 1. Cert-reload mechanism and lifecycle events | PLAN-embrace-tls-phase-01-reload.md | Not started |
| 2. mTLS for the `sf-database` gRPC channel | PLAN-embrace-tls-phase-02-database-mtls.md | Not started |
| 3. mTLS for the remaining inter-daemon gRPC channels | PLAN-embrace-tls-phase-03-other-grpc.md | Not started |
| 4. TLS for the MariaDB connection | PLAN-embrace-tls-phase-04-mariadb-tls.md | Not started |
| 5. Optional TLS for sf-api; document operator-LB story | PLAN-embrace-tls-phase-05-api-tls.md | Not started |
| 6. Cert-expiry monitoring (event log + prometheus) | PLAN-embrace-tls-phase-06-expiry-monitoring.md | Not started |
| 7. Repurpose `pki_internal_ca` as dev/test convenience | PLAN-embrace-tls-phase-07-dev-ca.md | Not started |
| 8. Push audit | PLAN-embrace-tls-phase-08-push-audit.md | Not started |

Notes on sequencing:

- **Phase 0 is research-and-decide.** The number of open
  questions above means we're not ready to write code. The
  output of phase 0 is a Decisions section appended to this
  master plan, and the phase table above probably gets
  re-cut.
- **Phase 1 is the universal pre-requisite.** Cert reload
  has to work before mTLS-everywhere is operationally
  acceptable, because otherwise every rotation is a planned
  cluster restart.
- **Phase 2 (database mTLS) is the canary.** It's the
  highest-traffic gRPC channel and the one every daemon
  uses, so getting it right early de-risks the rest. If
  the design doesn't work for sf-database it doesn't work
  for anything.
- **Phases 3-5 are parallel-eligible** once phase 2's
  pattern is established, but probably better sequenced
  serially to keep CI green.
- **Phase 8 is the push audit.** It runs `PUSH-AUDIT.md`
  over the accumulated diff of every phase in this plan
  against `develop`, not the last phase's diff alone.
  Findings land as their own pull request, and the plan
  is not complete until each is resolved or declined in
  writing here. If the audit finds nothing, that is
  recorded in one sentence.

## Dependencies on other plans

This plan **depends on `PLAN-remove-primary.md` having
landed** — specifically, the operator-provides-PKI surface
that emerges from the BYO-MariaDB / galaxy-role phases.
Trying to do mTLS-everywhere while the deployer still
controls its own internal CA would mean inventing PKI
inside SF, which is exactly the wrong direction.

It is technically possible to start phase 0 (research and
decisions) in parallel with the remove-primary work, since
phase 0 produces no code. Phases 1 onward should wait until
remove-primary is at least past phase 6 (galaxy-role
packaging).

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The workflow mirrors
`PLAN-remove-primary.md`: plan in the management session,
spawn a sub-agent per implementation step, review the
diff in the management session, fix or retry, commit when
satisfied. See that plan's "Agent guidance" section for the
full statement.

This plan is **TLS-heavy and security-relevant**, so it
skews higher-effort and worktree-isolated than the average
implementation work. Cert handling errors are subtle and
sometimes silent (e.g. "the connection succeeded but no
peer cert was verified"). Sub-agents on phases 2, 3, 4 should
default to **opus at high effort in a worktree**.

### Planning effort

The master plan itself is **high effort** by virtue of the
volume of open questions. Phase 0 (decisions) is high
effort with significant external research. Phases 1, 2, 4
are high effort (cross-daemon, security correctness).
Phases 3, 5, 6 are medium-to-high once the pattern from
phase 2 is established. Phase 7 (dev CA repurposing) is
medium.

### Step-level guidance

Each phase plan should include a table of steps with effort,
model, isolation, and brief, in the same format as
`PLAN-remove-primary.md`. The same effort and model
guidance applies.

### Management session review checklist

After a sub-agent completes a TLS-related step, the
management session should verify, **in addition to the
usual checks**:

- [ ] Peer verification actually happens — the connection
      fails when given a wrong-CA cert in a test.
- [ ] Cert reload works without dropping in-flight RPCs (or
      drops them cleanly with a documented retry path).
- [ ] The error path on cert expiry is user-friendly: an
      event log line and a metric, not a stack trace.
- [ ] CI deploys still succeed end-to-end. mTLS-everywhere
      with a broken dev-CA path is worse than no mTLS at
      all because it breaks every developer's local rig.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* All gRPC channels between SF daemons use mTLS, with peer
  certificates verified against the operator-provided CA
  bundle and (per phase 0's decision) optional SAN
  validation.
* The SF-to-MariaDB connection uses TLS with verification.
* sf-api supports TLS termination on its own listener; the
  documentation describes both end-to-end and
  proxy-terminated patterns.
* Every SF daemon reloads its TLS material without restart
  (mechanism per phase 0's decision).
* Cert expiry within an operator-configurable window emits
  both an event log event and a prometheus metric.
* The `pki_internal_ca` role is documented as a dev/test
  convenience only and is not run in the default deploy.
* `pre-commit run --all-files` passes.
* The cluster_ci rig deploys and passes end-to-end with
  mTLS enabled.
* Documentation in `docs/operator_guide/` describes the PKI
  requirements, expected cert SANs, on-disk layout, and
  rotation procedure.

### Future work

- **Client-cert auth to MariaDB.** If we decide in phase 0
  that we only verify the server cert, leaving SF-to-DB
  client auth on passwords, then "MariaDB client cert auth"
  becomes future work to revisit when the operator MariaDB
  landscape supports it more uniformly.
- **Hardware-backed keys.** Some operators will want SF's
  certs to come from a PKCS#11 store / TPM / HSM rather
  than disk. Out of scope for this plan but a reasonable
  next iteration.
- **Short-lived certs / SPIFFE-style identity.** A natural
  next step once mTLS is everywhere; out of scope here.

### Bugs fixed during this work

This section should list any bugs we encounter during
development that we fixed.

### Documentation index maintenance

When creating a new master plan from this template, update
the following files in `docs/plans/`:

* **`index.md`** — add one row to the *Master plans* table.
* **`order.yml`** — add an entry for the new master plan.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
