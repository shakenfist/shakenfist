# Kerbside VDI tokens phase 7: Shaken Fist mint-path functional test

This is phase 7 of a **cross-repository master plan** whose plan of record
lives in the Shaken Fist repository
(`shakenfist/docs/plans/PLAN-kerbside-vdi-tokens.md`, branch
`vdi-console-tokens`). After the scope narrowing described below, phase 7 is
**entirely Shaken Fist side**: a functional test that mints a VDI console
token and verifies it offline, exactly as kerbside would, with no kerbside
in the loop. It lands in `shakenfist-wt-vdi-tokens`, branch
`vdi-console-tokens`.

## Scope decision (why this is smaller than the master plan's phase 7)

The master plan's phase 7 originally bundled a **full cross-repo
end-to-end** test: deploy a real single-node Shaken Fist, mint a token via
`/vdiconsoleproxy`, exchange it at a real kerbside, and drive a proxied
SPICE session. That was pulled out into **phase 9** (run once the four PRs
are on `develop`) because kerbside CI installs Shaken Fist and the client at
each repo's `develop` HEAD, so the loop is red until the changes merge.

This phase then intended a second piece, **7b**: exercise the kerbside
`/sf-console.vv` exchange end to end in the cloud-free `direct-qemu` lane by
seeding a keypair and minting a token locally, on the theory that the
exchange is offline by design so no Shaken Fist is needed. Grounding that in
the code showed it **cannot work in the direct-qemu lane** as it stands:

- `sf_token.verify_sf_token` only trusts sources of `type: shakenfist`
  (`_shakenfist_source_names()` filters on the type), but the direct-qemu
  lane's `direct-qemu-lab` source is `type: static`. A token attributed to
  a static source is never verifiable — the happy path itself 401s with
  `unknown signing key`.
- The exchange then looks the console up and asserts
  `console['source'] == claims['source']` (`api.py`, the `SfToken`
  resource), so the console must live under the **verifying shakenfist
  source's** name, not the static source's.
- A hand-inserted console under a shakenfist source name does not survive:
  the daemon's maintenance loop (`_parse_sources`, at start and every 60s)
  reaps any console a live scrape did not yield.

Making a `type: shakenfist` source genuinely hold a scraped console plus
published signing keys requires a real cluster to talk to
(`get_cluster_cacert`, `get_vdi_token_public_keys`, `get_nodes`,
`get_instances`) — i.e. a **real Shaken Fist** or a mock SF REST server
faithful to the client SDK. A mock is heavy and brittle against SDK drift,
and the exchange's real value (a working proxied session from an
SF-minted token) is exactly what **phase 9** proves against a real SF.

Separately, `KERBSIDE_URL` is read into `config` at Shaken Fist **process
start** (`load_cluster_config()` → `SFConfig()`), so it cannot be flipped on
at test runtime; it needs deploy-time provisioning, which phase 9's real
kerbside deployment supplies anyway.

**Decision (maintainer):** the kerbside offline-exchange + proxy lane and
its `KERBSIDE_URL` provisioning move to **phase 9**, alongside the real
cross-repo end-to-end. The kerbside exchange is already exhaustively covered
**offline** by unit tests (`tests/unit/test_sf_token.py` — nine adversarial
cases; `tests/unit/test_api.py::SfTokenApiTestCase`), and the proxy relay of
a kerbside-issued `.vv` is already proven by the main direct-qemu lane, so
the pre-merge gap this leaves is narrow.

Phase 7 therefore keeps only the piece that is **greenable pre-merge and
self-contained in Shaken Fist**: the mint-path functional test (7a).

## Situation (grounded)

- Functional ("cluster") tests live in
  `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/` and run against a
  **real deployed cluster** via the actions reusable workflow
  `smoke-cluster.yml`, driven through the REST API by the
  `shakenfist_client` SDK. The shared fixture is
  `deploy/shakenfist_ci/base.py`: `BaseTestCase` builds an admin
  `system_client`; `BaseNamespacedTestCase` builds a scoped
  `self.test_client` via `_make_namespace(name, key)`. Cross-namespace
  pattern to copy: `test_object_names.py`
  (`test_instance_same_name_different_namespace`) makes a second scoped
  client and asserts per-namespace isolation. `_await_instance_create`
  waits for the `created` state (right for a diskless VM, which never
  prints console text).
- The endpoints under test:
  `InstanceVDIProxyConsoleHelperEndpoint` (`external_api/instance.py`,
  route `/instances/<ref>/vdiconsoleproxy`) — mints via
  `vdi_tokens.mint_console_token(..., audience=config.KERBSIDE_URL,
  issuer=config.ZONE, duration=config.KERBSIDE_TOKEN_DURATION)`, returns
  `{url, expires_at}`, 404s if `KERBSIDE_URL` unset, 406 if not
  `created`, 409 if not a SPICE console, 500 if no signing key; and
  `AdminVDITokenPublicKeyEndpoint` (`external_api/admin.py`, route
  `/admin/vditokenpubkey`) — returns `vdi_tokens.public_view(material)`
  (`{active_kid, keys:[{kid, alg, public_pem, created}]}`) to any
  authenticated token.
- **The client SDK at `develop` has no method for either endpoint** (only
  `get_vdi_console_helper`); those methods live on the unmerged phase-4
  branch. So the functional test calls `client._request_url('GET',
  '/instances/<uuid>/vdiconsoleproxy')` and `.../admin/vditokenpubkey`
  **directly**, which keeps 7a independent of the client PR's merge order.
- Minted-token claims (`util/vdi_tokens.py`): header `kid`; claims
  `iss`, `aud`, `sub`, `sf:namespace`, `iat`, `exp`, `jti`; algorithm
  `EdDSA`. The `.vv` exchange URL is
  `<KERBSIDE_URL>/sf-console.vv?token=<jwt>`.

## Decisions

1. **7a calls `_request_url` directly, not a client method** — green at
   `develop` HEAD regardless of the client PR.
2. **7a is self-describing about the deployment.** The exchange URL is split
   back into its base and token; the token's `aud` is read from the token
   itself and asserted equal to that base, so nothing about the specific
   `KERBSIDE_URL` value is hard-coded.
3. **7a skips cleanly when the feature is off.** A 404 (no `KERBSIDE_URL`)
   or 500 (no signing key) on the owning namespace's mint means the feature
   is unprovisioned on this cluster; the test `skipTest`s rather than
   failing a cluster that legitimately runs the feature off. The ownership
   assertion runs only **after** the happy path returns 200, so it is never
   a vacuous pass on a cluster where every mint 404s.
4. **Provisioning `KERBSIDE_URL` for CI is deferred to phase 9.** Because
   `KERBSIDE_URL` is process-cached at Shaken Fist start it cannot be set at
   test runtime; phase 9's real kerbside deployment configures it, at which
   point 7a activates for real. Until then 7a lands green as a skip — a
   reviewed, ready test that documents the mint contract. No
   `shakenfist/actions` change in this phase.

## Execution

### Shaken Fist side (worktree `shakenfist-wt-vdi-tokens`, branch `vdi-console-tokens`)

`deploy/shakenfist_ci/cluster_ci_tests/test_vdi_tokens.py`:
`class TestVDIConsoleTokens(base.BaseNamespacedTestCase)` with
`namespace_prefix='vditokens'`.

- Create a diskless instance (`video` unset → the server's default SPICE
  console; minimal disk, no base image) and `_await_instance_create`.
- **Happy path:** `self.test_client._request_url('GET',
  '/instances/<uuid>/vdiconsoleproxy').json()`; on `ResourceNotFoundException`
  or `InternalServerError`, `skipTest` (feature off). Assert the URL carries
  the `/sf-console.vv?token=` marker and `expires_at` is in the future; split
  out the base and token. Fetch `/admin/vditokenpubkey`; pick the key whose
  `kid` matches the token header; read `aud` from the token unverified; then
  `jwt.decode(token, public_pem, algorithms=['EdDSA'], audience=aud)` and
  assert `sub == uuid`, `sf:namespace == this namespace`, `iss` present,
  `jti` present, and the URL base equals the audience.
- **Ownership:** a second scoped client requesting the first namespace's
  instance raises `ResourceNotFoundException` (mirror `test_object_names.py`).
- Attach debug context with `self.addDetail`.

## Success criteria

* A namespaced client mints a `/vdiconsoleproxy` token whose signature
  verifies against `/admin/vditokenpubkey`, with correct
  `sub`/`iss`/`aud`/namespace/`exp`; a foreign namespace cannot mint for
  another's instance. Runs green on the existing functional matrix with no
  kerbside and no client-PR dependency (skipping cleanly until phase 9
  provisions `KERBSIDE_URL`).
* No private key material or token string is logged. `tox` stays green; no
  `shakenfist/actions` change.

## Dependencies and out of scope

* **Moved to phase 9:** the kerbside offline-exchange + proxy lane (formerly
  7b) and its `KERBSIDE_URL` CI provisioning, folded into the full cross-repo
  end-to-end (real single-node Shaken Fist → client mint → kerbside exchange
  → proxied session).
* **Out of scope → phase 8:** operator/user docs for the whole flow.
