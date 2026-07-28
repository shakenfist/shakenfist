# Phase 2 — namespace keys as first-class objects

Parent plan:
[PLAN-auth-federation.md](PLAN-auth-federation.md).

## Scope

Phase 2 promotes namespace keys from anonymous entries in the
`namespace_attributes.keys` JSON column to `DatabaseBackedObject`s
with their own tables, lifecycle, events, expiry surfaced through
the API, and reaping. After this phase ships, a key is something an
operator can list with its expiry and provenance, expired keys are
actually removed rather than accumulating invisibly, and no secret
material is written to the event log. Behaviour visible to existing
clients is unchanged.

The phase covers:

- A behaviour-preservation test suite, written and committed
  *before* any production change, pinning today's semantics.
- The `NamespaceKey` object: Pydantic models, tables, three-layer
  accessors, protos, database-daemon handlers, the DBO class, and
  an iterator with SQL-pushdown find.
- A one-shot, version-gated data migration out of the JSON column
  (the `node_daemon_states` precedent), run by
  `sf-ctl ensure-mariadb-schema`.
- Cutover of every consumer: `Namespace.keys`/`add_key`/
  `remove_key`/`external_view`, `get_api_token` (service keys),
  `/auth` minting, `verify_token`'s nonce check, and the key CRUD
  endpoints — including an additive `expiry` API parameter and
  fixes for two latent bugs in the key-update PUT endpoint.
- Reaping of expired and soft-deleted keys.
- Removing secret material (whole JWTs, stored key hashes) from
  all auth-related events.
- Documentation and glossary updates.

Out of scope (deferred):

- Scopes *enforcement* and everything federation: phase 3. The
  scopes column exists after this phase but is NULL (= unscoped,
  wildcard behaviour) for every key.
- `sf-client` changes (client-python is a separate repository);
  this phase delivers the API-side `expiry` support and records
  the client work as follow-up.
- Dropping the legacy `keys` JSON column: a later schema bump,
  per the `node_daemon_states` precedent ("left in place until
  nothing reads it").
- Retiring the legacy `_service_key` exact-name nonce bypass in
  `verify_token` (see "Decisions" below).

## Design

### Object shape

`NamespaceKey` follows the AgentOperation pattern (the closest
"small DBO owned by a parent" exemplar,
`shakenfist/operations/agentoperation.py:20`): a frozen static
model plus a mutable attributes model, both Pydantic-generated
tables via `pydantic_to_sqlalchemy_table`
(`shakenfist/schema/sqlalchemy.py:370`).

- **Static** (`namespace_key_data.py`, frozen): `uuid`
  (SQLNativeUUID, PK), `namespace` (SQLIndex — the owning
  namespace's name, matching `namespaces.name`), `name` (the key
  name; unique index on `(namespace, name)`), `version`.
- **Attributes** (`namespace_key_attributes.py`, mutable, keyed by
  uuid): `key` (the base64-encoded bcrypt hash), `nonce`, `expiry`
  (nullable float epoch), `scopes` (nullable JSON list; NULL means
  unscoped/wildcard — phase 3 formalises the vocabulary),
  `provenance` (nullable JSON dict; phase 3's exchange writes the
  mapping-rule reference and satisfied claims).

Hash and nonce live in *attributes* because key update (rotation)
mutates them. JWT identity strings stay `<namespace>:<keyname>` —
tokens reference keys by name, so outstanding tokens survive the
migration untouched.

`ObjectType.NAMESPACE_KEY` gets a fresh, never-reused `proto_id`
(`shakenfist/schema/object_types.py:107-144`). `state_targets`:
initial → created → deleted (→ hard-deleted via the standard
reaper); no error state — key operations are atomic.

### Migration (resolves master plan open question 7)

The codebase answers the one-shot-vs-dual-read question for us:
migrations are folded into `_ensure_*_schema` version steps, run
only by `sf-ctl ensure-mariadb-schema` (`shakenfist/client/ctl.py:190`),
and `sf-database` *refuses to start* if the schema is behind
(`daemons/database/main.py:5208`). New code therefore never runs
against unmigrated data, and **no dual-read window is needed**.

- Tables land at v1; the v1→v2 step runs
  `_migrate_keys_from_namespace_attributes(engine)`: read every
  `namespace_attributes.keys` JSON blob, fan `nonced_keys` entries
  out as rows (`INSERT ... ON DUPLICATE KEY UPDATE`, idempotent),
  preserving name, hash, nonce, and expiry; migrated keys get
  NULL scopes and NULL provenance. Modelled exactly on
  `_migrate_daemon_states_from_node_attributes`
  (`shakenfist/mariadb.py:8926`).
- Expired entries are migrated too (the reaper deletes them on its
  first pass) so the migration needs no policy.
- The JSON column is left in place, unread, until a later version
  drops it.
- **Rollback caveat (documented, accepted):** rolling back to
  pre-phase code revives the JSON column, which still holds every
  pre-migration key; keys created or rotated *after* migration
  exist only in the table and are invisible to rolled-back code.
  Same trade the `node_daemon_states` migration made.

### Hot path

`verify_token` today does a namespace static read plus a whole
`namespace_attributes` row load per request for the nonce compare
(`external_api/base.py:171-213`). After cutover it does the
namespace static read plus a point read of one key row via the
unique `(namespace, name)` index — equal or cheaper. Per the master
plan's open question 7 caution: **no caching of key lookups**; a
stale cache would delay nonce revocation.

`/auth` minting iterates a namespace's keys bcrypt-comparing each
(`auth.py:95-101`); this becomes an indexed
`find keys where namespace = X` returning the same set. Note the
reaper actively shrinks this set over time (today expired service
keys accumulate and are bcrypt-compared forever — an existing
performance leak this phase also fixes).

### Service keys

`get_api_token` (`namespace.py:292-314`) is the sole producer of
`_service_key_<rand>` keys: 5-minute expiry, minted at most every
~285 s per worker per namespace thanks to the in-process
`CACHED_TOKENS` cache. That volume is modest, so service keys
become ordinary key objects — no special storage path. Their
lifecycle events are equally modest (a create event a few times an
hour per busy namespace). The reaper (below) finally deletes them
after expiry, fixing the current unbounded accumulation in the
attributes row.

### Reaping

Two mechanisms, both existing patterns:

1. A cluster-daemon scheduled task (pattern:
   `delete_stale_cluster_operation_targets`,
   `daemons/cluster/main.py:100-102`) sweeps keys with
   `expiry < now - grace` and *soft-deletes* them (state →
   deleted, with an "expired" audit event on the key). Grace
   default: `config.CLEANER_DELAY`-scale, new config option
   `NAMESPACE_KEY_REAP_GRACE` (expired keys are already
   unusable at exactly `expiry` via the read-time filter — the
   sweep is hygiene, so the grace period only controls how long
   the corpse is visible in listings).
2. `per_deleted_object_checks`
   (`daemons/cluster/scheduled_tasks.py:355`) hard-deletes
   long-soft-deleted keys automatically once `NamespaceKey` is in
   `OBJECT_NAMES_TO_CLASSES`.

Enforcement remains check-at-use (design principle 6): `/auth` and
`verify_token` filter expiry exactly, in the query or in code, and
never depend on the reaper's cadence.

### Decisions

1. **The legacy `_service_key` exact-name nonce bypass is
   preserved** (`base.py:195`, pinned by
   `AuthWithServiceKeyTestCase`). It predates nonced keys and some
   deployment may still hold such a token shape. Retirement is
   recorded for phase 3's endpoint-security pass (where scoped
   tokens make the audit natural) — not silently dropped here.
2. **Key-name validation is made consistent**: today namespace
   creation rejects the exact name `service_key` (`auth.py:166`)
   while key creation rejects the `_service_key` prefix
   (`auth.py:288`). Both paths will reject both patterns; this is
   a behaviour change only for names nobody can have legitimately
   wanted.
3. **The mint event stays on the namespace** (message unchanged)
   so operator-facing event streams keep their shape; it simply
   stops embedding the JWT. Per-key "used for mint" events are
   *not* added — event volume would double for no audit gain,
   since the namespace event already names the key.
4. **The two PUT-endpoint bugs are fixed, not preserved**
   (`auth.py:353` membership-tests the wrong dict level;
   `auth.py:358` passes a namespace *string* where the object is
   expected). The endpoint appears to have never worked and has
   zero test coverage; fixing it is safe precisely because no
   working client can depend on it. New tests pin the fixed
   behaviour.
5. **Events that currently leak secrets** lose exactly the secret
   fields, nothing else: `create_token` (`access_tokens.py:28-34`)
   and `log_token_use` (`base.py:225-233`) drop `token`; the
   namespace-creation events (`auth.py:180-198`) drop `token`; the
   invalid-key event (`auth.py:103-109`) drops `key-body`.
   Messages, event types, and remaining extras are unchanged.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | opus | none | Behaviour-preservation tests, committed before any production change. Extend `shakenfist/tests/external_api/test_auth.py` (and add `shakenfist/tests/test_namespace_keys.py` if cleaner) to pin: (i) `add_key(expiry=...)` stores expiry and the `keys` accessor filters an expired key (`namespace.py:165-194`); (ii) an expired key can no longer mint via `/auth` nor validate via `verify_token` (mock `time.time`); (iii) nonce mismatch → 401 (mint a token, rotate the key via `add_key` same name, replay the old token against a `verify_token`-guarded endpoint); (iv) `_namespace_keys_putpost` rejects `_service_key`-prefixed names (`auth.py:288`); (v) the legacy exact-name `_service_key` bypass in `verify_token` (`base.py:195`) — extend `AuthWithServiceKeyTestCase`; (vi) `get_api_token` creates a `_service_key_*` key with ~300 s expiry and reuses the cached token on a second call (`namespace.py:289-314`); (vii) `external_view()` returns `keys` as a list of names with no hashes/nonces (`namespace.py:239-256`). Do NOT test the PUT key-update endpoint (known broken; fixed in 2e). Style: mock-heavy like the existing `AuthKeysTestCase`. Run `tox` targets per CLAUDE.md. Commit subject: "tests: pin namespace key behaviour before the DBO migration." |
| 2b | high | opus | none | Storage layer. Following the AgentOperation recipe exactly: (i) `shakenfist/schema/namespace_key_data.py` (frozen; uuid `SQLNativeUUID` PK, namespace `SQLIndex`, name, version) and `namespace_key_attributes.py` (mutable; uuid PK, key, nonce, expiry nullable float, scopes nullable JSON, provenance nullable JSON), field markers per `schema/sqlalchemy.py:140-173`; unique index on (namespace, name) — check `SQLUniqueIndex` (`sqlalchemy.py:149`) supports composite or add the index in the ensure function like the hand-built indexes in `_ensure_agent_operations_schema` (`mariadb.py:13881`). (ii) In `mariadb.py`: `_get_namespace_keys_table`/`_get_namespace_key_attributes_table` (pattern `:13836/:13863`), `_ensure_*_schema` (pattern `:13881/:13918`), `NAMESPACE_KEYS_VERSION` constant near `:199`, entries in `EXPECTED_SCHEMA_VERSIONS` (`:252`), `ensure_schema()` (`:2477-2512`), `register_all_tables()` (`:2524`). (iii) Three-layer accessors mirroring `:13960-14395`: create/get/update/delete for both tables, plus `_direct_find_namespace_keys(namespace, include_expired: bool, now: float)` doing the indexed per-namespace listing with optional `expiry IS NULL OR expiry > now` SQL filter, and `_direct_delete_expired_namespace_keys(older_than)` (pattern `_direct_delete_stale_transfers`, `mariadb.py:7808`). (iv) protos/database.proto messages + RPCs (pattern `:1594-1645`, `:228-236`), then `tox -e genprotos` (never grpc_tools directly), commit regenerated stubs together. (v) daemons/database/main.py handlers (pattern `:3104-3198`) and Monitor operations list entries (`:4931`, entries near `:5023`). Unit tests for schema-up idempotency and accessor routing, mirroring the eventlog phase 1 test shapes. Commit subject: "mariadb: add namespace_keys tables, accessors, and RPCs." |
| 2c | high | opus | none | The object. New `shakenfist/namespace_key.py`: `NamespaceKey(dbo)` with `object_type = ObjectType.NAMESPACE_KEY` (add member with fresh proto_id in `schema/object_types.py:107-144`), `initial_version`/`current_version`, `state_targets` {initial→created, created→deleted, deleted→hard_deleted per baseobject conventions}, `_db_create`/`_db_get` (pattern `operations/agentoperation.py:57/85`), `new(namespace, name, plaintext_secret, expiry=None, scopes=None, provenance=None)` that bcrypt-hashes exactly as `namespace.py:180` does today and generates the nonce via `sfrandom.random_id()`, `rotate(plaintext_secret)` (new hash + new nonce under `get_lock_attr`), `external_view()` (never the hash or nonce), `delete()`/`hard_delete()` (drop both rows; pattern `agentoperation.py:188-194`), and `NamespaceKeys(dbo_iter)` with SQL-pushdown `_find` calling the 2b find accessor (pattern `instance.py:2079`). Register in `OBJECT_NAMES_TO_CLASSES` so `per_deleted_object_checks` (`daemons/cluster/scheduled_tasks.py:355-407`) reaps soft-deleted keys. Unit tests: state transitions, rotation changes nonce, external_view redaction. Commit subject: "objects: add the NamespaceKey DatabaseBackedObject." |
| 2d | medium | opus | none | Migration. `_migrate_keys_from_namespace_attributes(engine)` in mariadb.py modelled line-for-line on `_migrate_daemon_states_from_node_attributes` (`:8926-9014`): read each `namespace_attributes` row, fan `keys['nonced_keys']` entries into the new tables (uuid4 per key; INSERT ... ON DUPLICATE KEY UPDATE on the (namespace, name) unique index; preserve key/nonce/expiry verbatim; scopes and provenance NULL; object_states row 'created' per migrated key), count (migrated, errors), leave the JSON column untouched. Wire as the v1→v2 step of `_ensure_namespace_keys_schema`. Unit test with a mocked engine: one namespace with two keys (one expired) → two rows; re-run is a no-op. Update docs/operator_guide/database.md's schema-changes section per its conventions. Commit subject: "mariadb: migrate namespace keys out of the attributes JSON column." |
| 2e | high | opus | none | Cutover. (i) `namespace.py`: `keys` accessor, `add_key`, `remove_key`, `external_view`, and `get_api_token` re-point at NamespaceKey objects (accessor returns the same `{'nonced_keys': {...}}` shape initially so `/auth` and `verify_token` call-sites can be reviewed one at a time — or refactor call-sites to a cleaner `lookup_key(name)`; implementer's choice, but the wire-visible `external_view` and endpoint responses must be byte-identical, proven by 2a's tests). Remove the now-dead whole-blob `get_lock_attr('keys', ...)` read-modify-write; per-key uniqueness now comes from the DB index (handle IntegrityError on duplicate add as the existing behaviour dictates — today add_key overwrites; preserve overwrite-as-rotation semantics explicitly). (ii) `external_api/base.py` `verify_token`: replace the attributes-row load with a point read of the named key; preserve the exact-name `_service_key` bypass (`:195`) and all error semantics. (iii) `external_api/auth.py`: `/auth` POST iterates the find-accessor results; key CRUD endpoints move to NamespaceKey; ADD the additive optional `expiry` body parameter (float epoch; validated > now) to key create/update; FIX the PUT endpoint bugs (`:353` membership check, `:358` str-vs-object) with new tests; align `service_key`/`_service_key` validation per Decision 2; fix the swagger examples that say `key_names` (the real field is `keys`). Run the FULL 2a suite unchanged — it must pass without modification (the checklist's bit-compatibility proof). Commit subject: "auth: serve namespace keys from the key objects." |
| 2f | medium | sonnet | none | Reaper. New scheduled task in the cluster daemon (`daemons/cluster/main.py` maintenance preamble, pattern `:100-102`): fetch keys with `expiry < now - config.NAMESPACE_KEY_REAP_GRACE` (new config option in `config.py` near `CLUSTER_OPERATION_TARGET_RETENTION` `:265`, default 3600, `0` disables), soft-delete each via the object (state → deleted) with an "expired" audit event on the key. Hard delete then happens via `per_deleted_object_checks` automatically (verify with a test that a deleted key older than CLEANER_DELAY is hard-deleted by `_process_per_deleted_object_queue`, pattern `scheduled_tasks.py:388-407`). Unit tests: sweep soft-deletes only sufficiently-expired keys; disabled when config is 0. Commit subject: "cluster: reap expired namespace keys." |
| 2g | low | sonnet | none | Secrets out of events. Exactly Decision 5's four sites: `util/access_tokens.py:28-34` drop `'token'` from the extra dict; `external_api/base.py:225-233` (`log_token_use`) drop `'token'`; `external_api/auth.py:180-198` both namespace-creation events drop `'token'`; `auth.py:103-109` drop `'key-body'`. Nothing else about the events changes (messages, types, other extras). Update or add unit tests asserting the extras no longer contain the secrets (grep the tests for existing assertions on these events first — some pin the extra shape). Commit subject: "events: stop logging tokens and key material." |
| 2h | medium | sonnet | none | Docs and closeout. (i) `docs/developer_guide/authentication.md`: keys are now objects — describe lifecycle, expiry, rotation-changes-nonce, the reaper, and correct anything the cutover changed; (ii) `docs/developer_guide/api_reference/authentication.md`: document the `expiry` parameter and fix the `key_names`→`keys` examples; (iii) `docs/glossary.md`: the namespace key entry's expiry stops being future tense (scopes/provenance stay planned); (iv) `ARCHITECTURE.md` object-type list gains NamespaceKey; (v) master plan: mark open question 7 resolved (one-shot version-gated migration, no dual-read, rollback caveat documented), phase 2 → Complete in the Execution table and `docs/plans/index.md`; (vi) note the client-python follow-up (`--expiry` flag) in the master plan's Future work. Commit subject: "docs: namespace keys as first-class objects." |

After each step the management session runs
`pre-commit run --all-files`, reads the diff against the brief, and
confirms no unrelated edits. After 2e additionally: the full 2a
suite passed unmodified; a manual read of `verify_token` confirming
no caching crept in and the `_service_key` bypass survived. After
2h: `tox -e genprotos` is a no-op against the committed tree.

## Risks and mitigations

- **Risk:** the cutover subtly changes `/auth` or `verify_token`
  semantics (error codes, key-iteration order, expired-key
  handling).
  **Mitigation:** 2a's suite is written against the *old* code and
  must pass unmodified after 2e; the step ordering makes this
  mechanical to verify.
- **Risk:** service-key concurrency changes — today `add_key`
  serialises on one cluster lock per namespace; per-row inserts
  race instead.
  **Mitigation:** the unique `(namespace, name)` index is the
  arbiter; service-key names are random so collisions are
  negligible; user add_key preserves overwrite-as-rotation
  explicitly. 2e's brief calls this out.
- **Risk:** migration on a large deployment (many namespaces ×
  accumulated service keys) is slow inside
  `sf-ctl ensure-mariadb-schema`.
  **Mitigation:** volumes are small in practice (keys are counted
  in tens, not millions, even with the leak); the migration is a
  single pass with idempotent inserts and can be re-run.
- **Risk:** rolled-back code loses post-migration keys (rollback
  caveat).
  **Mitigation:** documented in the operator guide's upgrade
  notes; pre-existing keys are unaffected; the exposure window is
  one upgrade cycle. Precedent accepted for `node_daemon_states`.
- **Risk:** event-shape changes (2g) break an operator's log
  tooling that greps for the removed fields.
  **Mitigation:** the removed fields are secrets that should never
  have been logged; the event messages and types are unchanged;
  release notes flag it.

## Definition of done

- [x] 2a's behaviour-preservation suite exists in a commit that
      predates every production change, and passes unmodified at
      phase end. `test_namespace_keys.py` was never edited after
      2a; `test_auth.py` has additions only.
- [x] `namespace_keys` + `namespace_key_attributes` tables exist,
      version-gated, with the migration as the v1→v2 step; the
      legacy JSON column is untouched and unread.
- [x] `NamespaceKey` is a DBO with events, soft delete, expiry,
      NULL scopes/provenance placeholders, and SQL-pushdown
      listing; registered for the standard hard-delete reaper.
- [x] `/auth`, `verify_token`, key CRUD, `external_view`, and
      `get_api_token` all serve from the key objects; wire shapes
      byte-identical except the additive `expiry` parameter and
      the fixed PUT endpoint.
- [x] Expired keys are soft-deleted by the cluster daemon sweep
      and hard-deleted by the existing reaper; enforcement never
      depends on either.
- [x] No event carries a JWT or key hash; asserted by tests. Went
      further than planned: the tests assert the secret appears in
      *no* event under any key, which found a fifth site the plan
      had not counted (raw request/response bodies logged by the
      API request tracing, so `POST /auth` recorded the caller's
      plaintext key). Bodies are no longer logged under `/auth`.
      The token nonce was also removed, after the operator agreed
      it should not be published either.
- [x] `pre-commit run --all-files` clean; `tox -e genprotos`
      no-op (verified by md5sum over the generated stubs before
      and after); unit tests green at 1438.
- [ ] Functional CI green on the branch. **Not yet run** — the
      branch has not been pushed, so no CI has ever executed
      against it. This is the one outstanding item at phase end.
- [x] Master plan open question 7 marked resolved; phase status
      updated in the Execution table and `docs/plans/index.md`;
      glossary and guides updated.
- [x] Each step is a single self-contained commit following
      project conventions including the Prompt paragraph and
      Co-Authored-By line. Two commits beyond the step plan: the
      nonce removal, and the master-plan phases 6 and 7 that step
      2g's findings prompted.

## Back brief

Before executing any step of this phase, the implementing
sub-agent should back-brief the management session on its
understanding of the brief and surrounding context. The management
session must review 2a's test list with the operator before 2b
begins, and present the 2e diff for operator review before commit —
it is the security-sensitive cutover.
