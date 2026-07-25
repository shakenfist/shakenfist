# Kerbside VDI tokens phase 2: the vdiconsoleproxy minting endpoint

This is phase 2 of
[PLAN-kerbside-vdi-tokens.md](PLAN-kerbside-vdi-tokens.md).
It delivers the first token: a REST endpoint that mints a
short-lived Ed25519-signed JWT for an instance the caller
owns, and returns a kerbside exchange URL. It builds directly
on [phase 1](PLAN-kerbside-vdi-tokens-phase-01-signing-key.md)'s
signing key (`shakenfist/util/vdi_tokens.py`,
`active_signing_key(get_signing_material())`).

Kerbside does not consume anything from this phase yet — that
is phases 5-6. This phase is provable end to end against a
unit test that verifies the minted token with phase 1's
published public key.

## Decisions

These refine the master plan's open questions 2, 5 and 8 for
the surface this phase builds, in the same way phase 1 refined
questions 1 and 8. They stand in for the not-yet-written
phase 0 for this endpoint.

1. **Two new config fields**, declared as Pydantic `Field`s on
   `SFConfig` (`shakenfist/config.py`) exactly like `DNS_SERVER`
   (`config.py:255`), read as `config.KERBSIDE_URL` /
   `config.KERBSIDE_TOKEN_DURATION`:

   - `KERBSIDE_URL: str = Field('', description=...)` — the
     kerbside deployment's public base URL, e.g.
     `https://kerbside.example.com`. Empty default means the
     feature is **off**. This doubles as the token `aud`
     (open question 4: kerbside rejects any token whose `aud`
     is not its own URL).
   - `KERBSIDE_TOKEN_DURATION: int = Field(300, description=...)`
     — token TTL in seconds (open question 2's recommended
     default).

   Both are cluster-wide "like `DNS_SERVER`": settable through
   `cluster_config`, which `load_cluster_config()` injects as
   `SHAKENFIST_*` env at process start. **Restart semantics**
   accepted: because the endpoint reads the `config` singleton
   (instantiated once at `config.py:670`), a change to either
   field is seen after the API daemon restarts — correct for
   deploy-time settings, and unlike the signing key (which
   phase 1 deliberately reads live via
   `mariadb.get_cluster_config()` so a freshly-created key
   needs no restart). No live-read is needed here: an operator
   turning the feature on restarts the API tier anyway.

2. **Minting lives in `util/vdi_tokens.py`** beside the phase 1
   key helpers, as one testable function:

   ```python
   def mint_console_token(
       instance_uuid: str, namespace: str, *,
       audience: str, issuer: str, duration: int,
   ) -> dict[str, Any]:
       ...  # returns {'token', 'jti', 'kid', 'expires_at'}
   ```

   It reads the active key via
   `active_signing_key(get_signing_material())`, builds the
   claim set below, and `jwt.encode(...)`s with
   `algorithm=SIGNING_ALG` and `headers={'kid': <active kid>}`.
   It takes `audience`/`issuer`/`duration` as **explicit
   arguments** rather than reading `config` itself, so it stays
   unit-testable without mocking the config singleton (the
   endpoint reads config and passes them). It raises
   `SigningKeyError` (re-used from phase 1) if no signing key
   exists, so the endpoint can translate that to a clear HTTP
   error. `jwt.encode` returns a `str` on PyJWT 2.x; no decode
   needed.

3. **Claim set** (normative; from the master plan's token-flow
   section):

   | Claim | Value |
   |-------|-------|
   | `iss` | `config.ZONE` (matches `util/access_tokens.py`; **verify the attribute name in 2a**) |
   | `aud` | `config.KERBSIDE_URL` |
   | `sub` | the instance UUID |
   | `sf:namespace` | the owning namespace — audit/display only; kerbside must not authorise from it |
   | `iat` | `int(time.time())` |
   | `exp` | `iat + duration` |
   | `jti` | `uuid.uuid4().hex` |
   | header `kid` | the active key's `kid` |

4. **Endpoint** `InstanceVDIProxyConsoleHelperEndpoint` in
   `shakenfist/external_api/instance.py`, modelled on
   `InstanceVDIConsoleHelperEndpoint` (`instance.py:1442`) but
   with a deliberately different decorator stack — **without**
   `@api_base.redirect_instance_request`:

   ```
   @swag_from(api_base.swagger_helper(...))
   @api_base.verify_token
   @api_base.arg_is_instance_ref
   @api_base.requires_instance_ownership
   @api_base.log_token_use
   def get(self, instance_ref=None, instance_from_db=None):
   ```

   Minting needs no node-local state (no SPICE CA file, no
   node ports), so the endpoint must answer from **any** API
   node; carrying `redirect_instance_request` would wrongly
   proxy it to the instance's hypervisor. Ownership is still
   enforced (`requires_instance_ownership`), which is the whole
   authorisation story (mission goal 1).

5. **Guards and their HTTP codes** (use `sf_api.error(code,
   msg)`, the file idiom):

   - `config.KERBSIDE_URL` empty → **404** `sf_api.error(404,
     'kerbside integration is not configured')`. Clean
     feature-off signal; a client probes the `vdi-console-proxy`
     capability first anyway.
   - Signing key absent (`get_signing_material()` is `None`,
     surfaced as `SigningKeyError`) → **500** naming the fix:
     `'kerbside signing key is not configured, run sf-ctl
     ensure-kerbside-signing-key'`. Distinct from the 404
     because `KERBSIDE_URL` being set means the operator
     intended the feature on, so a missing key is a real
     misconfiguration, not the off state.
   - Instance not `created` (`instance_from_db.state.value !=
     dbo.STATE_CREATED`) → **406** (matches base.py's
     "instance is not ready" 406 at `base.py:358`).
   - No SPICE console
     (`not instance_from_db.video['vdi'].startswith('spice')`)
     → **409** `'instance does not have a SPICE console'`.

6. **Response** — JSON body, never a 3xx (the Python client
   follows redirects silently, master plan situation note):

   ```json
   {"url": "<KERBSIDE_URL>/sf-console.vv?token=<jwt>",
    "expires_at": 1789000300}
   ```

   `expires_at` is epoch seconds (== the token `exp`), matching
   the rest of the token machinery (`created` in
   `vdi_tokens.py`). Build the URL by stripping any trailing
   slash from `config.KERBSIDE_URL` and appending
   `/sf-console.vv?token=<jwt>` (kerbside route decided in open
   question 8).

7. **Audit event** on the instance, never carrying the token:

   ```python
   instance_from_db.add_event(
       EVENT_TYPE_AUDIT, 'vdi console proxy token minted',
       extra={'jti': jti, 'kid': kid, 'namespace': namespace,
              'expires_at': expires_at})
   ```

8. **Capability** `vdi-console-proxy` added to the instance
   capability line in the root-page HTML
   (`external_api/app.py`, near the admin line phase 1 edited),
   so clients and kerbside can probe for support.

## Execution

All work in the `shakenfist-wt-vdi-tokens` worktree (branch
`vdi-console-tokens`), by sub-agents per the master plan's
model. Review each step's **actual files**, not its summary.
2b depends on 2a; 2c depends on 2a and 2b.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a   | high   | opus  | none      | Add `KERBSIDE_URL: str = Field('', ...)` and `KERBSIDE_TOKEN_DURATION: int = Field(300, ...)` to `SFConfig` in `shakenfist/config.py`, matching the `DNS_SERVER` field style (`config.py:255`). Add `mint_console_token(instance_uuid, namespace, *, audience, issuer, duration) -> dict` to `shakenfist/util/vdi_tokens.py` per decision 2/3: `import jwt`, build the claim set, sign with `active_signing_key(get_signing_material())`'s `private_pem`, `algorithm=SIGNING_ALG`, `headers={'kid': ...}`; return `{'token', 'jti', 'kid', 'expires_at'}`; raise `SigningKeyError` when no key. `jti = uuid.uuid4().hex`, `iat = int(time.time())`, `exp = iat + duration`. Verify `config.ZONE` exists and is the right issuer source (grep `util/access_tokens.py`); if the attribute differs, use the real one and note it. Do not read `config` inside the helper. Never log the token. `env -u FORCE_COLOR pre-commit run --all-files`. |
| 2b   | medium | sonnet | none     | Add `InstanceVDIProxyConsoleHelperEndpoint` to `shakenfist/external_api/instance.py` with the decorator stack in decision 4 (copy `InstanceVDIConsoleHelperEndpoint` at `instance.py:1442`, drop `@redirect_instance_request`). Implement the guards in decision 5 (order: config-off 404, then state 406, then SPICE 409; signing-key 500 comes from catching `SigningKeyError` around the mint call). Read `config.KERBSIDE_URL`/`config.KERBSIDE_TOKEN_DURATION`/`config.ZONE`, call `vdi_tokens.mint_console_token(instance_from_db.uuid, <owning namespace>, audience=..., issuer=..., duration=...)`, build the response in decision 6, emit the audit event in decision 7. Find the owning-namespace attribute on the instance (likely `instance_from_db.namespace` — verify) for the `sf:namespace` claim and audit `extra`. Register `GET /instances/<instance_ref>/vdiconsoleproxy` in `app.py` beside the `vdiconsolehelper` route (`app.py:355`). Add `vdi-console-proxy` to the instance capability line in the root HTML. `swag_from` with a realistic `{url, expires_at}` example. No token in any log or in the response beyond the `url`. `env -u FORCE_COLOR pre-commit run --all-files`. |
| 2c   | medium | sonnet | none     | Tests. In `shakenfist/tests/test_vdi_tokens.py` (extend), add `mint_console_token` unit tests: claims correct (`iss`/`aud`/`sub`/`sf:namespace`/`jti` present, `exp - iat == duration`), header `kid` == active kid, and the token **verifies** against phase 1's `public_view` public PEM via `jwt.decode(..., algorithms=['EdDSA'], audience=<aud>)`; and `SigningKeyError` when no key is configured. In `shakenfist/tests/external_api/` (extend the phase-1 `test_admin.py` sibling or add an instance-endpoint test matching that dir's harness): ownership enforcement — namespace A gets 403/404 minting for namespace B's instance (use the existing ownership-test pattern); feature-off 404 when `KERBSIDE_URL` empty; 500 when the key is absent; 406/409 state and SPICE guards; happy path returns a `url` containing `/sf-console.vv?token=` and an integer `expires_at`; the audit event is recorded; the response body contains no `private` material and no bare token field other than inside `url`. `env -u FORCE_COLOR pre-commit run --all-files`. |

## Success criteria

* `GET /instances/<ref>/vdiconsoleproxy` mints a token only for
  a caller who owns the instance; a non-owner is refused by
  `requires_instance_ownership` exactly as the direct
  vdiconsole endpoint refuses them.
* With `KERBSIDE_URL` empty the endpoint 404s cleanly; with it
  set but no signing key it 500s naming the `sf-ctl` command.
* The minted token carries the decision-3 claims, expires after
  `KERBSIDE_TOKEN_DURATION` seconds, and **verifies against the
  public key phase 1 publishes** at `/admin/vditokenpubkey`
  (this is the phase-5 kerbside contract, proven now).
* The response is a JSON `{url, expires_at}`, never a redirect;
  the URL points at `<KERBSIDE_URL>/sf-console.vv?token=<jwt>`.
* An audit event records `jti`/`kid`/`namespace` but never the
  token; no path logs the token or any private key material.
* `pre-commit run --all-files` (flake8, stestr, mypy) passes.

## Out of scope

Kerbside's consumption of the token (phases 5-6), the client
method and CLI launch (phase 4), pip-installable ryll
(phase 3), `host_subject` (phase 6), and documentation
(phase 8, though the config-field `description=` text and
docstrings land here). This phase does not touch kerbside.
