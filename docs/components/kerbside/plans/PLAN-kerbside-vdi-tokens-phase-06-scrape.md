# Kerbside VDI tokens phase 6: cluster-wide scrape and host_subject

This is phase 6 of a **cross-repository master plan** whose plan of
record lives in the Shaken Fist repository
(`shakenfist/docs/plans/PLAN-kerbside-vdi-tokens.md`, branch
`vdi-console-tokens`). It spans **two repos**: the kerbside scrape
(this repo, branch `sf-vdi-tokens`) and a Shaken Fist node change
(`shakenfist-wt-vdi-tokens`, branch `vdi-console-tokens`). Each half is
committed on its own repo's branch; this one plan covers both because
they are the producer/consumer of the same datum.

It makes the token flow actually reach real instances: phase 5's
`/sf-console.vv` 404s on any console kerbside has not scraped, and today
the Shaken Fist source scrapes only its own namespace and yields
`host_subject: None`.

## Situation (grounded)

- **The scrape** (`kerbside/sources/shakenfist.py:80`, `__call__`)
  already builds a node map — `system_client = self._make_client('system')`
  then `for node in system_client.get_nodes(): nodes[node['name']] = node`
  (`:87-90`) — and lists instances with the **namespaced** client
  `namespaced_client = self._make_client(self.args['username']); for inst
  in namespaced_client.get_instances():` (`:93-94`). It filters to
  `state == 'created'` + `video['vdi'].startswith('spice')` and yields a
  console dict whose last field is hardcoded `'host_subject': None`
  (`:116`), with `'hypervisor': inst['node']` and `'hypervisor_ip':
  nodes[inst['node']]['ip']`.
- **The client already supports what we need:** `get_instances(all=False)`
  (`client-python .../apiclient.py:509`) and `get_nodes()`/`get_node()`
  (`:1085`/`:1077`).
- **Periodic refresh is already implicit.** The maintenance loop
  re-instantiates each source every tick (`main.py` `_parse_sources` →
  `ShakenFistSource(**source)`), and phase 5 put the signing-key fetch in
  `__init__`, so keys (and the cacert) are already refetched every tick.
  Phase 6 adds no separate refresh timer — it only confirms this and
  avoids double-fetching.
- **Shaken Fist node facts:** nodes have a structured
  `NodeAttributesData` record (`shakenfist/schema/node_attributes.py:26`,
  MariaDB `node_attributes`) with fields like `last_seen`,
  `installed_version`, `is_hypervisor`; loaded/saved via
  `node._load_attributes()` / `_save_attributes(fields)`
  (`node.py:107-136`). Nodes are exposed by `/nodes` +
  `/nodes/<node>` (`external_api/app.py:407-408`,
  `external_api/node.py:67/119`).
- **The SPICE server cert** on each node is
  `/etc/pki/libvirt-spice/server-cert.pem` (deploy role
  `internal_ca/tasks/distribute_certificates.yml:52`), provisioned with
  `cn = hostname`. Its **subject** is exactly what the proxy's
  host-subject enforcement (kerbside PR #114 / ryll #166) matches on the
  backend TLS leg. `admin.py:58` already reads the sibling
  `ca-cert.pem`, so cert files are readable from the API/daemon context.

## Decisions

1. **Cluster-wide scrape under the system credential (open question 3,
   decided).** When the source's credential is the `system` namespace
   (`self.args['username'] == 'system'`), list instances with
   `system_client.get_instances(all=True)` instead of the per-namespace
   client, so every instance's console is scraped. When the credential is
   a specific namespace, keep `get_instances()` (that namespace only) —
   per-namespace scraping survives as the natural behaviour of a
   non-system credential, no separate filter flag needed. The node map
   already uses the system client, unchanged.

2. **host_subject provenance (open question 7): Shaken Fist publishes the
   real cert subject (option b), with kerbside tolerant of its absence.**
   - **Shaken Fist side:** each node records the subject of its
     `/etc/pki/libvirt-spice/server-cert.pem` into a new
     `NodeAttributesData` field `spice_server_cert_subject: str` (empty
     when no cert), and the node's external view exposes it so
     `get_nodes()` returns it alongside `ip`/`name`. The subject is read
     with `cryptography`'s x509 loader and rendered as the RFC4514/OpenSSL
     subject string the proxy compares against.
   - **kerbside side:** set `host_subject` in the scrape yield from
     `nodes[inst['node']].get('spice_server_cert_subject') or None`.
     Absence (an older Shaken Fist, or a node with no cert) leaves
     `host_subject = None`, which means the proxy skips host-subject
     enforcement for that backend exactly as it does today — a safe,
     forward-compatible default, never a hard failure.
   - **Interim/fallback (option a), opt-in:** a per-source config flag
     `synthesize_host_subject` makes kerbside synthesise
     `CN=<hypervisor hostname>` when the SF field is absent. Off by
     default (synthesis is brittle: it only matches the stock
     `cn=hostname` PKI, and an exact-match miss would wrongly *reject*
     the backend). It exists so an operator on the default PKI can turn
     host-subject enforcement on before the SF-side change is deployed.

3. **No periodic-refresh code (decision follows the situation).** Keys
   refresh via the existing per-tick source re-instantiation; phase 6
   only asserts this in a test and adds nothing. If a future change makes
   sources long-lived, revisit.

## Execution

Sub-agents; review actual files. The two repos are independent except
that kerbside step K2's populated `host_subject` is only non-null once SF
steps S1-S2 are deployed — K2 is written to tolerate absence, so it can
land first.

### Shaken Fist side (worktree `shakenfist-wt-vdi-tokens`, branch `vdi-console-tokens`)

| Step | Effort | Model | Brief |
|------|--------|-------|-------|
| S1 | medium | sonnet | Add `spice_server_cert_subject: str = Field('', ...)` to `NodeAttributesData` (`schema/node_attributes.py`) and an Alembic migration adding the column to `node_attributes` (follow the repo's migration skill). Expose the field in the node external view returned by `/nodes` + `/nodes/<node>` (`external_api/node.py`), so `get_nodes()` carries it. Unit tests for the schema default + external-view presence. |
| S2 | medium | sonnet | In `Node.observe_this_node` (`node.py`) — the per-node write of `last_seen`/`installed_version`/role flags — read `/etc/pki/libvirt-spice/server-cert.pem` (guard: absent on non-hypervisor nodes), and render its subject into `spice_server_cert_subject`, adding that field to the `_save_attributes` fields list. **The rendering must NOT use `rfc4514_string()`.** ryll's verifier (`shakenfist-spice-protocol` `host_subject.rs`, spice-common semantics) compares the certificate subject attribute-by-attribute in the certificate's own DER order, requiring the same count and types in the same order; `rfc4514_string()` renders attributes in reverse (most-specific-first), which the verifier's reorder check rejects. Instead iterate `cert.subject` in order, map each OID to its OpenSSL short name (`C, ST, L, O, OU, CN, DC, emailAddress`), escape `\` and `,` in values, and join with commas. Return None (disable pinning) if the subject carries an attribute with no accepted short name, since a partial rendering would wrongly reject the backend. A missing/unreadable/unparseable cert yields None, never crashes the observe loop. Test with fixture certs (order, escaping, CN-only, unnameable attr, missing file). |

### kerbside side (worktree `kerbside-wt-vdi-tokens`, branch `sf-vdi-tokens`)

| Step | Effort | Model | Brief |
|------|--------|-------|-------|
| K1 | low | sonnet | In `sources/shakenfist.py:__call__`, list instances cluster-wide when `self.args['username'] == 'system'`: use `system_client.get_instances(all=True)`; otherwise keep the per-namespace `get_instances()`. Preserve the existing state/vdi filters. |
| K2 | low | sonnet | In the same yield, set `'host_subject': nodes[inst['node']].get('spice_server_cert_subject') or None` (decision 2), plus the optional `synthesize_host_subject` per-source config path yielding `'CN=%s' % inst['node']` when the field is absent and the flag is set. Guard `nodes.get(inst['node'])` for an instance whose node is missing from the map. |
| K3 | medium | sonnet | Tests (`kerbside/tests/unit/`): the scrape uses `all=True` under a system credential and per-namespace otherwise (mock the client); `host_subject` is populated from a node carrying `spice_server_cert_subject`, is `None` when the node lacks it, and is `CN=<host>` when `synthesize_host_subject` is set; and a regression test asserting the source's per-tick re-instantiation re-runs the key fetch (documents decision 3). |

## Success criteria

* With a `system`-credential Shaken Fist source, kerbside scrapes every
  `created` SPICE instance cluster-wide (not just one namespace), so
  phase 5's `/sf-console.vv` finds the console instead of 404ing.
* When the Shaken Fist nodes publish `spice_server_cert_subject`,
  kerbside stores it as the console's `host_subject`, so the proxy's
  existing host-subject enforcement covers Shaken Fist backends; when
  they do not, `host_subject` is `None` and enforcement is skipped
  (status quo), never a hard failure.
* Signing keys stay fresh via the existing per-tick source
  re-instantiation; no duplicate fetch is added.
* `tox` passes in both repos; no token or private material is logged.

## Dependencies and out of scope

* K2's `host_subject` is only meaningful once S1-S2 ship; K1/K2/K3
  tolerate the SF field's absence, so the kerbside half can land and be
  released independently.
* Out of scope: the exchange endpoint (phase 5, done); functional/CI
  wiring that drives a real cluster-wide scrape end to end (phase 7);
  operator docs including the `KERBSIDE_URL`/audience coordination and
  the `synthesize_host_subject` knob (phase 8).
