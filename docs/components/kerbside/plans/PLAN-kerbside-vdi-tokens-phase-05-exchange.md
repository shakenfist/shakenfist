# Kerbside VDI tokens phase 5: the `/sf-console.vv` exchange endpoint

This is the kerbside side of phase 5 of a **cross-repository master
plan** whose plan of record lives in the Shaken Fist repository:

> `shakenfist/docs/plans/PLAN-kerbside-vdi-tokens.md`
> (branch `vdi-console-tokens` until merged)

It closes the loop: Shaken Fist phase 2 mints an Ed25519-signed JWT and
phase 4's client hands the exchange URL to a viewer; this phase adds the
kerbside endpoint that **validates that JWT entirely offline** and
issues kerbside's usual `.vv`, mirroring the `NovaToken` flow
(`kerbside/api.py:516`, route `/nova-console.vv`) but with the Keystone
callback replaced by an offline signature check.

## Situation (grounded)

- **`NovaToken` is the template** (`api.py:516`, registered
  `api.py:736`): a webargs `?token=` GET, mints a kerbside consoletoken
  (`consoletoken.create_token(source, uuid)`, `consoletoken.py:19`),
  and returns a proxy `.vv` built from `VIRTVIEWER_TEMPLATE`
  (`api.py:350`) with `password=<consoletoken>` and connection host/port
  from **kerbside config** (`config.PUBLIC_FQDN`,
  `PUBLIC_INSECURE_PORT`, `PUBLIC_SECURE_PORT`, `PROXY_HOST_SUBJECT`,
  `api.py:630-642`) — never from the token. Response mimetype
  `application/x-virt-viewer;charset=UTF-8`, no filename.
- **Key difference from Nova:** OpenStack is not scraped, so Nova
  *creates* the console (`db.add_console(...)`, `api.py:610`). Shaken
  Fist **is** scraped, so this endpoint **looks the console up** and
  404s if it is not (yet) scraped — it never creates one.
- **Offline verification needs cached keys.** The endpoint must not call
  Shaken Fist on the exchange path (that would reintroduce the
  availability coupling we are avoiding). Sources are ephemeral
  YAML-loaded objects instantiated in the maintenance loop
  (`main.py:124-137`), not a registry the API can reach. So the SF
  signing public keys are **cached in the kerbside DB** by the source at
  init/refresh and read by the endpoint — kerbside's DB-only-IPC rule.
- **`consoles` is keyed by `uuid` alone** (`db.py:168`, `get_console`
  `db.py:280` filters only on `Console.uuid`). We look up by the token's
  `sub` and additionally assert the row's `source` matches the source
  whose key verified the token (defence against uuid reuse across
  sources).
- kerbside already pins **PyJWT 2.13.0 + cryptography 48.0.1**, so
  `jwt.decode(..., algorithms=['EdDSA'])` works with no new dependency.
  There is no existing `import jwt` in the repo — this introduces it.
- **Reaper pattern** (`_reap_expired_console_tokens`, `main.py:185`)
  and its 60s maintenance scheduling (`main.py:234-246`) are the model
  for the jti reaper. Alembic head is `f7b2e9c4a1d8`; the
  `session_terminations` migration
  (`alembic/versions/c4e7a1b9d2f3_...:33`) is the `create_table` style
  to match. The `.claude/skills/add-database-migration.md` skill governs
  migration + `db.py` model + `docs/schema.*` + tests.

## Decisions

1. **`SfToken` resource at `/sf-console.vv`** in `kerbside/api.py`,
   beside `NovaToken`: webargs `?token=` GET (matches the URL SF phase 2
   returns, `<KERBSIDE_URL>/sf-console.vv?token=<jwt>`). Same response
   shape as the Nova proxy `.vv`.

2. **Offline validation, in a new `kerbside/sf_token.py`**
   (`verify_sf_token(token) -> {source, sub, jti, exp}`, raising typed
   errors per rejection reason so the endpoint can audit + return the
   right code):
   - Parse the unverified header (`jwt.get_unverified_header`) for
     `kid`; require `alg == 'EdDSA'`.
   - For each configured `type == 'shakenfist'` source (iterate the
     `config.SOURCES_PATH` YAML as the Nova path does, `api.py:555`):
     load that source's cached keys from the DB (decision 4); if a key
     with the token's `kid` exists, `jwt.decode(token, public_pem,
     algorithms=['EdDSA'], audience=<expected aud>)`. PyJWT enforces
     signature, `aud`, and `exp` in that one call. First source whose
     decode succeeds **is** the console's source.
   - **Unknown kid → refetch once:** if no cached key matches the kid,
     refresh every shakenfist source's keys from SF once (decision 4's
     fetch), update the DB, and retry. Still no match → reject. This is
     the only path that touches SF, and only on rotation.
   - Distinct typed failures: unknown-kid-after-refetch, bad signature,
     expired (`ExpiredSignatureError`), wrong aud
     (`InvalidAudienceError`), malformed. Never include the token in the
     error/audit text.

3. **Single-use `jti`** via a new `sf_token_jtis` table (`jti` String
   PK, `expiry` Float). After a good decode: if `sf_token_jti_exists(jti)`
   → reject as replay (401); else `add_sf_token_jti(jti, exp)`. A
   `_reap_expired_sf_token_jtis` reaper mirrors
   `_reap_expired_console_tokens` (`main.py:185`) and is scheduled in the
   60s maintenance block (`main.py:234-246`).

4. **Cached SF signing keys** in a new `sf_token_keys` table (`source`
   String PK, `keys_json` Text, `fetched_at` Float) holding SF's
   `public_view` payload (`{active_kid, keys:[{kid, alg, public_pem,
   created}]}`). `ShakenFistSource` gains `fetch_signing_keys()` —
   `self._make_client('system').get_vdi_token_public_keys()` (the phase-4
   client method, beside `get_cluster_cacert()`, `shakenfist.py:32`) —
   which upserts the row; called at `__init__`. (Periodic refresh and
   cluster-wide scrape are **phase 6**; phase 5 does init-time fetch +
   the endpoint's refetch-once, which is all verification needs.) The
   endpoint reads keys from the DB only.

5. **Audience** = `https://<config.PUBLIC_FQDN>` by default, with an
   optional `SF_CONSOLE_TOKEN_AUDIENCE` config override (empty →
   derived) for deployments whose public URL differs (port/scheme/path).
   The operator must set Shaken Fist's `KERBSIDE_URL` to exactly this
   value — it is both the token `aud` and the base of the exchange URL.
   Record the coordination requirement in phase 8 docs.

6. **Console lookup** by `sub`: `c = db.get_console(source, sub)`; if
   `c is None` or `c['source'] != source` → 404 (`'console not found'`).
   Backend addressing for the proxy comes from that row and from
   kerbside config, never from the token.

7. **Issue exactly as Nova:** `consoletoken.create_token(source, sub)`,
   then the proxy `.vv` (config-based host/port, `password=token['token']`),
   returned with the Nova mimetype. **Audit** accepted
   (`db.add_audit_event(source, sub, token['session_id'], None, None,
   None, 'Shaken Fist console token exchanged')`) and rejected exchanges
   (reason, no token text) via the same helper (`db.py:718`).

8. **Admin-UI exposure unchanged (open question 6, recorded):** the
   existing Keystone-group `/console/proxy/...` admin path still works
   for scraped SF consoles; restricting it is explicitly out of scope
   here.

## Execution

All in the `kerbside-wt-vdi-tokens` worktree (branch `sf-vdi-tokens`),
by sub-agents. Review each step's **actual files**. 5b needs 5a; 5c
needs 5a+5b; 5d needs all.

| Step | Effort | Model | Brief |
|------|--------|-------|-------|
| 5a | medium | sonnet | Per `.claude/skills/add-database-migration.md`: one Alembic migration (`down_revision = 'f7b2e9c4a1d8'`) creating `sf_token_jtis` (`jti` String PK, `expiry` Float) and `sf_token_keys` (`source` String PK, `keys_json` Text, `fetched_at` Float), matching the `session_terminations` style. Add both models + accessors to `kerbside/db.py`: `add_sf_token_jti(jti, expiry)` (raise a typed `ReusedJti` on PK collision), `sf_token_jti_exists(jti)`, `reap_expired_sf_token_jtis()` (delete `expiry < now`, return reaped rows), `upsert_sf_token_keys(source, keys_json, fetched_at)`, `get_sf_token_keys(source)`. Update `docs/schema.md`/`docs/schema.html`; unit tests in `kerbside/tests/unit/`. `tox -e py3` + `tox -e flake8`. |
| 5b | high | opus | Add `kerbside/sf_token.py` with `verify_sf_token(token) -> dict` per decisions 2-5 (first `import jwt` in the repo; raise typed exceptions: `UnknownKid`, `BadSignature`, `Expired`, `WrongAudience`, `Malformed`). Reads cached keys via `db.get_sf_token_keys`; the refetch-once path calls a module hook that refreshes all shakenfist sources' keys. Add `ShakenFistSource.fetch_signing_keys()` to `kerbside/sources/shakenfist.py` (client `get_vdi_token_public_keys()`, upsert to DB) and call it from `__init__` beside the cacert fetch. Add the `SF_CONSOLE_TOKEN_AUDIENCE` config field + the `PUBLIC_FQDN`-derived default helper. NEVER log the token or any error text containing it. Unit-testable: keys and sources injectable/mocked. `tox`. |
| 5c | high | opus | Add `SfToken(sf_api.Resource)` to `kerbside/api.py` beside `NovaToken`, route `/sf-console.vv` (`api.py:736` block): webargs `?token=`; call `sf_token.verify_sf_token`; map its typed errors to 401 with distinct messages + a rejected audit event; jti replay check+insert (decision 3); console lookup + source-match 404 (decision 6); `consoletoken.create_token` + proxy `.vv` mirroring `api.py:630-642`; accepted audit event. Import the shakenfist source into api.py as needed. Add `_reap_expired_sf_token_jtis` to `kerbside/main.py` (mirror `main.py:185`) and schedule it in the 60s block (`main.py:234-246`) and at startup (`main.py:253`). `tox`. |
| 5d | medium | sonnet | Adversarial unit tests in `kerbside/tests/unit/` (match the repo's harness): replay (second exchange of the same jti → 401), expired token, wrong `aud`, unknown `kid` **without** a successful refetch (→ 401) and **with** (refetch supplies the key → success), forged signature (signed by a different key → 401), and unscraped/absent console (→ 404). Plus the reaper (expired jti removed; live one kept) and `fetch_signing_keys` (client return upserted to DB). Sign test tokens with a locally generated Ed25519 key via PyJWT, mirroring shakenfist's `test_vdi_tokens.py` crypto tests. `tox -e py3` + `tox -e flake8`. |

## Success criteria

* A JWT minted by Shaken Fist phase 2 (signed by the active cluster key,
  `aud` = kerbside's audience) is exchanged at `/sf-console.vv` for a
  valid proxy `.vv` **with no call to Shaken Fist**, provided the
  console is scraped and the `jti` is unused.
* Replayed `jti`, expired token, wrong `aud`, forged signature, and
  unknown `kid` (after a single refetch) are all rejected with an audit
  event and no `.vv`; an unscraped console 404s.
* Backend addressing in the issued `.vv` comes from config + the scraped
  console row, never from token claims.
* The `jti` reaper removes expired entries on the maintenance tick; no
  token or key private material is ever logged.
* `tox -e py3` and `tox -e flake8` pass.

## Dependencies and prerequisites

* **Phase-4 client method:** `fetch_signing_keys()` calls
  `get_vdi_token_public_keys()`, added to `shakenfist_client` in phase 4,
  so kerbside must run a `shakenfist_client` that has it (a phase-4
  client release, or a git pin) — the same ordering the master plan
  already notes for phase 6.
* **SF operational prereqs:** the SF cluster must have a signing key
  (`sf-ctl ensure-kerbside-signing-key`, phase 1) and `KERBSIDE_URL` set
  to kerbside's audience (decision 5), for a live exchange to verify.

## Out of scope

Cluster-wide scraping, periodic key refresh, and `host_subject` for SF
consoles (phase 6); the SF minting side (phases 1-2, done); the client
(phase 4, done); functional/CI wiring (phase 7); operator docs
(phase 8, though config-field descriptions land here).
