# Kerbside VDI tokens phase 1: cluster signing key and pubkey publication

This is phase 1 of
[PLAN-kerbside-vdi-tokens.md](PLAN-kerbside-vdi-tokens.md).
It delivers the asymmetric signing key that phase 2's
`vdiconsoleproxy` endpoint will mint JWTs with, and the API
surface kerbside uses to fetch the corresponding public keys.
No token is minted in this phase.

## Decisions

These refine the master plan's open questions 1 and 8 for the
surface this phase builds. They are recorded here so the master
plan's phase 0 does not need to re-litigate them.

1. **Algorithm**: Ed25519, JWT `alg` value `EdDSA`, via PyJWT +
   `cryptography` (`cryptography` becomes a new direct
   dependency; PyJWT is already pinned at 2.13.0).
2. **Storage**: one `cluster_config` row named
   `KERBSIDE_JWT_SIGNING_KEY`, holding a JSON value:

   ```json
   {
     "active_kid": "3f2a9c1e",
     "keys": [
       {"kid": "3f2a9c1e",
        "private_pem": "-----BEGIN PRIVATE KEY-----...",
        "public_pem": "-----BEGIN PUBLIC KEY-----...",
        "created": 1789000000}
     ]
   }
   ```

   `keys` is newest-first and capped at two entries (current +
   previous) so rotation never invalidates in-flight tokens.
   `kid` is `uuid.uuid4().hex[:8]`.

   The name deliberately ends in `_KEY` so `sf-ctl`'s
   `SECRET_CONFIG_KEY_RE` (`shakenfist/client/ctl.py`) masks the
   value in `show-config` output unless `--show-secrets` is
   given.

   Consequence accepted: `load_cluster_config()`
   (`shakenfist/config.py:27`) injects **every** cluster_config
   row into `SHAKENFIST_*` environment variables at process
   start, so the private key is visible in the environment of
   every SF daemon, exactly as `AUTH_SECRET_SEED` (which gates
   strictly more power) already is. Consumers in this plan never
   read it from the environment — they call
   `mariadb.get_cluster_config()` at request time, which also
   means a key created after a daemon started is visible without
   a restart.
3. **No lazy generation.** The master plan's strawman had the
   API generate the key on first use. Dropped: `cluster_config`
   has no create-if-absent primitive, so racing API nodes could
   silently clobber each other's freshly generated keys, and
   adding one means proto + gRPC-tier churn this feature does
   not need. Instead generation is an explicit operator/deployer
   action: `sf-ctl ensure-kerbside-signing-key`, run once at
   deploy time (the deferred deployer phase will invoke it).
   Phase 2's endpoint returns a clear error when the row is
   absent. This matches the direction of carving bootstrap
   operations out of daemon startup (`ensure-mariadb-schema`).
4. **Rotation** is `sf-ctl rotate-kerbside-signing-key`:
   generate a new keypair, prepend it, mark it active, trim
   `keys` to two entries. Verifiers (kerbside) accept any
   published key; only the active one signs. The `ensure`
   command is idempotent — if the row exists it reports the
   active kid and changes nothing.
5. **Publication**: `GET /admin/vditokenpubkey` returning JSON
   `{"active_kid": ..., "keys": [{"kid", "alg", "public_pem",
   "created"}]}` — public halves only, never `private_pem`.
   Auth mirrors `/admin/cacert`
   (`external_api/admin.py:47`): `@verify_token` +
   `@log_token_use`, any authenticated namespace, deliberately
   no `@caller_is_admin` (kerbside authenticates as a service
   namespace; the material is public by design). When no key
   exists the endpoint returns 404 with a message naming the
   `sf-ctl` command. Advertised as `vdi-token-pubkey` in the
   root-page capability HTML (`external_api/app.py:206`).
6. **Key handling helpers** live in a new
   `shakenfist/util/vdi_tokens.py` so phase 2 (minting) and the
   two `sf-ctl` commands share one implementation. This module
   owns the JSON schema above; nothing else parses the row.

## Execution

All implementation in the `shakenfist-wt-vdi-tokens` worktree
(branch `vdi-console-tokens`), by sub-agents per the master
plan's execution model. Steps 1b and 1c depend on 1a; 1d
depends on all prior steps.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a   | high   | opus  | none      | Add `cryptography` to `pyproject.toml` (exact-pin style, alphabetical placement, licence comment like its neighbours). Create `shakenfist/util/vdi_tokens.py` with: `generate_keypair() -> dict` (Ed25519 via `cryptography.hazmat.primitives.asymmetric.ed25519`, PKCS8/SubjectPublicKeyInfo PEM, `kid = uuid.uuid4().hex[:8]`, `created` epoch int); `get_signing_material() -> dict | None` reading `KERBSIDE_JWT_SIGNING_KEY` via `mariadb.get_cluster_config()`; `ensure_signing_key() -> dict` (return existing row unchanged, else generate, `mariadb.set_cluster_config`, re-read and return what is actually stored); `rotate_signing_key() -> dict` (prepend new key, set `active_kid`, trim to 2, write, re-read); `public_view(material) -> dict` producing the endpoint response shape with `alg: 'EdDSA'` and no private members. Follow CLAUDE.md conventions (single quotes, 120 chars, `shakenfist_utilities.logs` logging, mypy type hints). Docstring the JSON schema. Do not log key material. |
| 1b   | medium | sonnet | none     | Add `ensure-kerbside-signing-key` and `rotate-kerbside-signing-key` click commands to `shakenfist/client/ctl.py`, mirroring the structure of `set-config` (`ctl.py:198`) and registering via `cli.add_command` (`ctl.py:432-440`). Both call the `util/vdi_tokens.py` helpers from step 1a and print only the active kid and key count — never PEM material. `ensure` notes idempotence in its help text; `rotate` warns that tokens signed by the dropped (third-oldest) key become unverifiable. Extend `shakenfist/tests/test_ctl.py` with tests for both commands using the existing mock-mariadb pattern. |
| 1c   | medium | sonnet | none     | Add `AdminVDITokenPublicKeyEndpoint` to `shakenfist/external_api/admin.py`, mirroring `AdminClusterCaCertificateEndpoint` (`admin.py:47`): `@swag_from(api_base.swagger_helper(...))` with a realistic response example, `@api_base.verify_token`, `@api_base.log_token_use`, no admin gate. Body: `public_view(get_signing_material())` from `util/vdi_tokens.py`; 404 with a message naming `sf-ctl ensure-kerbside-signing-key` when no key exists. Register the route as `GET /admin/vditokenpubkey` in `external_api/app.py` beside `/admin/cacert` (`app.py:258`), and add `vdi-token-pubkey` to the admin capability list in the root-page HTML (`app.py:206`). |
| 1d   | medium | sonnet | none     | Unit tests. In a new `shakenfist/tests/test_vdi_tokens.py` (mirror `test_cluster_config.py`'s mock-mariadb usage): ensure-then-ensure returns the identical kid and writes once; rotate produces a new active kid, retains the previous public key, caps `keys` at 2; a JWT signed with the active private key (PyJWT, `algorithm='EdDSA'`) verifies against the published public PEM and fails against the rotated-out third key; `public_view` output contains no `private` substring anywhere. In `shakenfist/tests/external_api/`, test the endpoint: 404 when unconfigured, response shape when configured, and assert the serialised response contains no private material. Run `pre-commit run --all-files` and fix findings. |

## Success criteria

* `sf-ctl ensure-kerbside-signing-key` creates the row once and
  is idempotent; `rotate-kerbside-signing-key` rotates with a
  two-key window; neither prints private material.
* `GET /admin/vditokenpubkey` serves the public view to any
  authenticated namespace, 404s helpfully when unconfigured,
  and is advertised as the `vdi-token-pubkey` capability.
* A JWT signed with the stored active key verifies with the
  published public key (proving the phase-2/kerbside contract).
* No code path logs, events, or serves `private_pem`.
* `pre-commit run --all-files` (flake8, stestr, mypy) passes.

## Out of scope

Token minting and `KERBSIDE_URL` (phase 2), kerbside's
consumption of the pubkey endpoint (phases 5-6), client method
for fetching the pubkeys (phase 4), operator documentation
(phase 8, though docstrings and `--help` text land here).
