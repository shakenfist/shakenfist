# Sticky session affinity for SF blob transfers

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read
relevant source files, understand existing patterns (the
REST API in `shakenfist/external_api/`, the blob and blob
transfer flows in `shakenfist/blob.py` and
`shakenfist/daemons/transfers/`, how the shakenfist client
in the sibling `client-python` repo issues blob reads and
writes including byte-range retry on failure), and ground
your answers in what the code actually does today. Do not
speculate about the codebase when you could read it
instead. Where a question touches on external concepts
(HTTP cookie scoping, RFC 6265, load-balancer session
affinity features in HAProxy / nginx / nginx-plus /
Envoy / AWS ALB / GCP / Azure), research as needed to give
a confident answer. Flag any uncertainty explicitly rather
than guessing.

All planning documents should go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the system architecture
overview and the blob storage and transfer subsystems.
Consult `CLAUDE.md` for build commands and project
conventions. Key references inside the repo include
`shakenfist/blob.py`, `shakenfist/external_api/blob.py`
(or equivalent — confirm during phase 0 research),
`shakenfist/daemons/transfers/main.py`, and the
`shakenfist_client.apiclient` blob read/write path in the
sibling `client-python` repo. Phase 0 research should also
confirm exactly which HTTP methods and paths the SF client
uses for chunked uploads and ranged downloads, because the
cookie-scoping decisions in this plan depend on those URL
shapes.

This plan is a **placeholder**. It captures intent and the
known open questions and is intentionally light on detail.
Phase 0 will resolve the open questions into a decisions
section and the phase table below will be re-cut accordingly.

When we get to detailed planning, I prefer a separate plan
file per detailed phase, named for the master plan with
`-phase-NN-descriptive` appended before the `.md` extension.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit.

## Situation

`PLAN-remove-primary.md` establishes that when sf-api on a
given node is asked for a blob it doesn't hold, the
receiving node opens a connection to a peer that does and
streams bytes through to the client without staging the
blob to local storage. The bandwidth cost is a double-hop
on the cluster mesh, which is typically an order of
magnitude faster than the operator's outer network, so the
cost lands where bandwidth is cheap.

This streaming-proxy baseline is correct for **single-
request** transfers. For **multi-request** transfer
sessions, there is a tighter answer that eliminates the
double-hop entirely for the session, without breaking the
operator-perimeter property that clients only ever see the
load balancer's URL.

Two SF transfer patterns are multi-request sessions:

1. **Multi-chunk uploads.** A large blob upload is split
   across many HTTP requests, all of which logically
   belong to one upload operation.
2. **Ranged downloads with retry on failure.** The SF
   client (`shakenfist_client`) issues byte-range reads
   and retries from the failure point if the connection
   drops. This is functionally the same shape as a
   multi-request session.

For both patterns, if the LB can be told "send the next
request from this client to *that* backend," SF can pick
the right backend on the first request and have every
subsequent request land directly on it. No proxy, no
double-hop, no client-visible cluster topology.

The HTTP mechanism that supports this without exposing
node identity is **server-set sticky cookies**: the chosen
backend emits `Set-Cookie: <name>=<opaque-value>`; the LB
intercepts incoming Cookie headers with that name and
routes to the matching backend. The cookie value is
opaque to the client; the perimeter property is preserved.

Every major LB supports this pattern, but **the concrete
shape differs per LB**:

- **HAProxy** (open source): native via `cookie SERVERID
  insert indirect nocache` on the backend, with each server
  line carrying a `cookie <value>` token that the server
  emits. Mature, widely deployed.
- **nginx-plus** (commercial): `sticky cookie` and
  `sticky route` directives.
- **Envoy:** `stateful_session` filter with cookie-based
  affinity.
- **AWS ALB:** application-controlled stickiness via
  target-set `AWSALB_TG_*` cookies.
- **GCP / Azure LB:** equivalent features under different
  names.
- **Open-source nginx:** *does not natively support this*.
  Available via third-party modules
  (e.g. `nginx-sticky-module-ng`) or via the `hash`
  directive with content-addressable URLs as a workaround.
  This is the operator's-LB-today situation for some SF
  deployments and is a real constraint.

The lack of a single wire-level standard means SF cannot
ship a "works on every LB out of the box" implementation.
The implementation surface is small — sf-api emits a
Set-Cookie, sf-api honours one on inbound — but the
operator's LB has to be configured for it and the cookie
format may have to match what the LB expects.

## Mission and problem statement

Shaken Fist supports server-set sticky session affinity
for multi-request blob transfers (multi-chunk uploads and
ranged downloads), eliminating the cluster-mesh
double-hop for those sessions without exposing per-node
URLs to clients. The streaming-proxy baseline from
`PLAN-remove-primary.md` remains the universal fallback for
operators whose LB does not support server-set sticky
cookies.

Concretely:

- sf-api emits a `Set-Cookie` on the first request of a
  multi-request transfer session, identifying the backend
  that should own the session.
- sf-api honours an inbound matching cookie by recognising
  itself as the named backend (or refusing if the named
  backend doesn't match, falling through to streaming
  proxy).
- The cookie's name, value format, and scope are decided
  during phase 0 with reference to the major LBs we want
  to support.
- Documentation describes the LB-side configuration
  required for each supported LB, plus the documented
  fallback behaviour for LBs that don't support this
  pattern.
- The SF client handles the cookie transparently — it does
  not need to know what the cookie means or which backend
  it points at.

The principle is: **make the LB do what it's already good
at, and avoid teaching it anything new about the cluster.**
The LB sees a cookie and routes by it. SF decides which
backend should own each session and tells the LB via the
cookie value.

## Open questions

This plan is light on detail because almost every concrete
decision depends on a phase 0 research pass. The open
questions include at least:

1. **Cookie name and value format.** Configurable? Default
   to a SF-specific name like `SF-STICKY`? Value: opaque
   per-node identifier? Path or query of the session?
   This decision interacts with every LB we want to
   document.
2. **Cookie scoping.** Per-transfer-session via `Path=`?
   Per-blob? Cluster-wide? The interaction with concurrent
   transfers from one client matters: two simultaneous
   multi-chunk uploads to different blobs should be able to
   land on different backends.
3. **Which LBs do we officially document?** HAProxy and
   Envoy are easy. nginx-plus we can document but few
   operators have it. AWS ALB and GCP/Azure equivalents
   for the cloud-deployed SF operators. **Open-source
   nginx is the real question** — Mikal's own deployment
   uses it, and it doesn't natively support this. Options:
   document a third-party module, document a `hash`-based
   workaround using content-addressable URLs, or accept
   that the streaming-proxy fallback is the answer for
   nginx-OSS users.
4. **Backend identity in the cookie.** Should the cookie
   value be the node UUID, the node hostname, an operator-
   configured server name (HAProxy-style), or an opaque
   hash? Each has implications for cluster topology
   changes (add/remove nodes).
5. **Choosing the backend on the first request.** When a
   first chunk arrives on a random node, does that node
   always become the owner, or do we want to pick by
   content-addressable placement (potentially streaming
   the first chunk to the chosen owner and being sticky
   from chunk #2 onward)? The latter integrates better
   with the eventual content-addressable placement
   direction noted in `PLAN-remove-primary.md`.
6. **Fallback detection.** How does sf-api know that
   stickiness *isn't* working — i.e., the LB isn't honoring
   the cookie? Naive answer: it can't, and falls through
   to streaming proxy transparently when a request for
   session X lands on a node that isn't the owner. That's
   probably fine, but worth being explicit about the
   behaviour.
7. **Backend failover during a session.** If the sticky
   backend dies mid-upload, the LB's health check moves
   traffic away. The next chunk lands on a different node
   which is not the session owner. What's the recovery
   path — start the upload over from the client, or salvage
   what was already received? Interacts with how SF
   represents in-progress uploads.
8. **Interaction with future content-addressable
   placement.** If blobs eventually live on
   `placement_hash(blob) mod cluster`, sticky cookies and
   consistent-hash routing solve overlapping problems.
   Worth deciding whether sticky transfers is a stepping
   stone toward, or a parallel mechanism with, that
   future state.
9. **Client cooperation.** Does the SF Python client
   handle path-scoped cookies correctly on its session
   object? Most HTTP libraries do, but worth verifying
   before phase 1. Same question for other language
   bindings, eventually.
10. **Does this require any wire-protocol changes to the
    SF REST API?** Most likely not — adding `Set-Cookie`
    response headers and reading inbound `Cookie` headers
    is backwards-compatible. Confirm during phase 0.

## Execution

Provisional, to be re-cut after phase 0.

| Phase | Plan | Status |
|-------|------|--------|
| 0. Research and decisions document | PLAN-sticky-transfers-phase-00-decisions.md | Not started |
| 1. sf-api emits and honours sticky cookies | PLAN-sticky-transfers-phase-01-server.md | Not started |
| 2. LB configuration docs for the supported LBs | PLAN-sticky-transfers-phase-02-lb-docs.md | Not started |
| 3. Client verification and any necessary client-side adjustments | PLAN-sticky-transfers-phase-03-client.md | Not started |
| 4. Failover behaviour and partial-upload recovery | PLAN-sticky-transfers-phase-04-failover.md | Not started |
| 5. Push audit | PLAN-sticky-transfers-phase-05-push-audit.md | Not started |

**Phase 5 — push audit.** Runs `PUSH-AUDIT.md` over the
accumulated diff of every phase in this plan against
`develop`, not the last phase's diff alone. Findings land as
their own pull request, and the plan is not complete until
each is resolved or declined in writing here. If the audit
finds nothing, that is recorded in one sentence.

## Dependencies on other plans

- **`PLAN-remove-primary.md` must have landed** at least
  through the phase that delivers the streaming-proxy
  baseline for blob reads. The streaming proxy is this
  plan's universal fallback; without it, the
  sticky-cookie path has no graceful failure mode.
- This plan is **parallel-compatible with
  `PLAN-embrace-tls.md`.** mTLS does not interact with
  HTTP cookie semantics (cookies live in HTTP headers
  inside the TLS-protected channel). Either plan can land
  first or they can land in parallel.
- The future content-addressable placement work (out of
  scope here, belongs in the blob-storage roadmap) is a
  natural successor or alternative to this plan, and
  phase 0 should think about how the two relate before
  committing to a specific cookie / placement design.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The workflow mirrors
`PLAN-remove-primary.md`: plan in the management session,
spawn a sub-agent per implementation step, review in the
management session, fix or retry, commit when satisfied.

This work touches the operator-LB interface, which is a
configuration surface SF doesn't control. Sub-agents
working on phases 0-2 should be skewed toward **opus at
high effort** because mis-specifying the LB integration is
costly to undo once operators have committed configs.

### Planning effort

The master plan itself is **medium effort** — it's a
placeholder. Phase 0 (research and decisions) is high
effort with significant external research about LB
features. Subsequent phases will be re-evaluated once
phase 0 lands.

### Step-level guidance

Each phase plan should include a step table in the same
format as `PLAN-remove-primary.md`, with effort, model,
isolation, and brief columns.

### Management session review checklist

Standard checklist from `PLAN-remove-primary.md`, plus:

- [ ] Document additions describe each supported LB's
      required configuration with enough specificity that
      an operator can paste it in.
- [ ] The fallback path (streaming proxy when stickiness
      is unavailable) is exercised by tests, not just
      asserted in the docs.
- [ ] Behaviour with a missing or wrong-cookie session
      degrades gracefully — no requests are dropped, just
      proxied.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* sf-api emits a server-set sticky cookie on the first
  request of a multi-request blob transfer session.
* sf-api recognises the cookie on subsequent requests and
  serves the session locally when it is the named backend,
  or falls through to streaming proxy when it is not.
* The streaming-proxy fallback continues to work for
  operators whose LB does not honour the cookie.
* HAProxy, Envoy, and at least one major cloud LB
  configuration is documented and verified end-to-end.
* The open-source nginx situation is documented honestly,
  with a clear statement of which deployment paths are
  supported and which fall back to streaming proxy.
* The SF Python client handles cookies correctly without
  per-call cooperation — `apiclient` carries them across a
  session transparently.
* Failover during a sticky session is well-defined: either
  the upload resumes on the new owner or the client gets a
  clean error and can restart.
* `pre-commit run --all-files` passes.

### Future work

- **Content-addressable placement with consistent-hash
  routing.** This is the deeper version of the same idea:
  rather than picking a backend per session, the placement
  function decides where blobs live and the LB hashes URLs
  consistently. Sticky cookies and consistent-hash routing
  may eventually converge in design; out of scope here.
- **Per-language SF client bindings.** This plan's
  client-side verification focuses on the Python client.
  As other-language bindings appear, cookie handling needs
  re-verification per binding.

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
