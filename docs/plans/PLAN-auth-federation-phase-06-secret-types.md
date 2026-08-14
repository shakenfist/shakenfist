# Phase 6 — Secrets that cannot be logged by accident

Planning effort: **high**. The master plan sets no effort for this
phase. High is warranted because the survey turned the phase from a
prophylactic type change into the fix for a live credential leak, and
because two of the mechanical-looking edits (the SQL column type and
the config sentinel comparison) fail *silently* if done the obvious
way. Neither is visible in a diff review without knowing what
`SecretStr` does to `==` and to the table generator.

## Scope

Make the codebase's secret-carrying fields refuse to render
themselves, so that stringifying one into a log line or an event
produces `**********` rather than the credential.

In scope:

* `NamespaceKeyAttributesData.key` and `.nonce` become
  `pydantic.SecretStr`, with the persistence layers and the four
  consumer sites unwrapping explicitly.
* `shakenfist/schema/sqlalchemy.py` learns the `SecretStr` column
  mapping, without which the change silently alters the table's DDL.
* The three secret-carrying `SFConfig` fields become `SecretStr`.
* Stopping the live leak the survey found, first and separately.
* Test hardening, so the assertions that guard this cannot be
  repaired into passing vacuously.

Out of scope, explicitly:

* **The minted plaintext key secret.** See Decision 6.
* **Retiring the legacy `nonced_keys` accessor shape.** Phase 2 pinned
  it deliberately with behaviour-preservation tests; converting `/auth`
  off it is that cutover's work, not this phase's.
* **gitleaks and the standing Loki query.** That is phase 7. This
  phase stops secrets reaching a sink; phase 7 detects the ones that
  got out.
* **Rotating the credentials the survey found in Loki, and purging
  them from log storage.** An operational action on a running cluster,
  not a code change. Recorded below so it is not lost.
* **`docs/plans/order.yml`**, which never carries phase files.

## What the survey found

The master plan's phase 6 section makes six checkable claims. Four
hold. Two are wrong in ways that change the work, and the survey
additionally found a live credential leak of exactly the shape this
phase exists to prevent. The false claims are corrected at source in
the master plan's phase 6 section and in `docs/plans/index.md` as part
of the commit that adds this file, so a later step need not redo it.

### The claim that holds

`NamespaceKeyAttributesData.key` and `.nonce` exist with those names
and are `Annotated[str, Field(max_length=255)]`
(`shakenfist/schema/namespace_key_attributes.py:57-61`). `SecretStr`
enforces `max_length` on the wrapped value, so those constraints
survive the change unaltered — verified by probe.

### A live credential leak, in production, today

`shakenfist/daemons/queues/startup_tasks.py:248-249` logs every
configuration item at INFO on `sf-queues` startup:

```python
for key, value in config.model_dump().items():
    LOG.info(f'Configuration item {key} = {value}')
```

`AUTH_SECRET_SEED` (`config.py:162`) and `MARIADB_PASSWORD`
(`config.py:866`) are plain `str`, so this writes the cluster's JWT
signing seed and its database password out in full. INFO and above is
shipped to Loki (`shakenfist/logship.py:24`), so both leave the node
and land in log aggregation.

This is not theoretical. A Loki query for `Configuration item
AUTH_SECRET_SEED` over the last 30 days returns the seed in plaintext
from `sf-1` through `sf-6`, most recently at 2026-08-14T16:50; the
same query for `MARIADB_PASSWORD` fills a 500-line limit. Reachability
is direct: `shakenfist/daemons/queues/main.py:199` calls
`startup_tasks()`.

Three things follow. First, this is a sixth site of step 2g's exact
bug, found by the mechanism phase 7 proposes rather than by review, in
a file nobody thought to look at because the leak is not in the auth
code. Second, it is the strongest available argument for this phase:
`SecretStr` on those two fields makes the f-string render asterisks
with no change to the log line at all. Third, the master plan's
framing of phase 6 as "deliberately independent of the federation
work and could be done at any time" is now wrong, and is corrected.

The credentials involved are already exposed and a code fix does not
un-expose them. Rotation and log purging are an operator action,
recorded in Future work.

### `SecretStr` silently changes the table DDL

The master plan says the generator "learns that `SecretStr` maps to a
string column". It does not say what happens if that step is
forgotten, and the answer is not "it fails".

`SecretStr` is not a `BaseModel` subclass, so `_is_complex_type()`
declines it (`shakenfist/schema/sqlalchemy.py:307-318`), `'uuid'` does
not appear in its `str()` so `_is_uuid_type()` declines it too
(`:293-296`), and it is absent from `PYTHON_TO_SQLALCHEMY` (`:255-261`).
Execution therefore reaches the fallback at `:365-367`, which emits a
`LOG.warning` and returns `mysql.LONGTEXT()`. Probed against the real
function:

```
plain    -> String(length=255)
secret   -> LONGTEXT()
```

The consequence is worse than a wrong column type, because
`_ensure_namespace_key_attributes_schema()` creates the table from the
model only when it is absent (`shakenfist/mariadb.py:12489-12503`) and
has no `ALTER` path. A fresh install would get `LONGTEXT`, every
upgraded cluster would keep `VARCHAR(255)`, and with no schema version
bump `verify_schema_versions` has nothing to complain about. The
divergence would be invisible until something depended on the length
limit.

Mapping `SecretStr` to `sa.String(255)` keeps the generated DDL
byte-identical, which is why this phase needs no migration and no
version bump.

### The unwrap sites are six, not the three named

The master plan names "the bcrypt comparison in `/auth`, the nonce
comparison in `verify_token`, and the JWT claim in `create_token`".
One of those three is accurate as written; the other two are one hop
away from where the plan puts them, and the plan omits the persistence
boundaries entirely.

* **`verify_token`** — accurate. `nonce = key.nonce` reads the
  property, then compares against the claim
  (`shakenfist/external_api/base.py:730-736`).
* **`/auth`'s bcrypt compare** — reads `keys[keyname]['key']`
  (`shakenfist/external_api/auth.py:186-189`), an entry in the legacy
  `nonced_keys` dict. The model field is unwrapped one level up, at
  `shakenfist/namespace.py:200`, where the accessor rebuilds that dict
  from the rows. That matters: an untyped `dict[str, Any]` is
  precisely the shape that stringifies without complaint, so where the
  boundary is drawn decides whether `/auth` is inside the protection
  or outside it. Decision 4.
* **`create_token`'s claim** — the function takes `nonce: str`
  (`shakenfist/util/access_tokens.py:18`) and is handed
  `keys[keyname]['nonce']` from that same dict
  (`external_api/auth.py:196`).
* **Two SQL writes** — `sa.insert(...).values(key=data.key,
  nonce=data.nonce)` at `mariadb.py:12849-12850` and the update at
  `:12909-12910`.
* **The gRPC boundary** — `_namespace_key_attrs_to_proto()` at
  `mariadb.py:12973-12975` and `_from_proto()` at `:13000-13003`.

A positive result worth stating: neither namespace-key persistence
path goes through `model_dump()`. Both name their columns field by
field, so there is no route by which `model_dump_json()` could persist
the literal string `**********` — the silent catastrophe this change
could otherwise introduce. The generic upgrade path at
`baseobject.py:273` does use `model_dump()`, but this table has no
upgrade steps (`mariadb.py:12507-12510`).

### Config secrecy today is a name regex at one boundary only

The master plan calls `AUTH_SECRET_SEED` "the obvious other
candidate" without noting that cluster config already has a secrecy
mechanism: `SECRET_CONFIG_KEY_RE` in `shakenfist/client/ctl.py:152-157`,
which `show-config` uses to redact by default. It matches on key
*name*, deliberately over-matching — it also catches the three
integer fields `API_TOKEN_DURATION`, `FEDERATION_MAX_TOKEN_BYTES` and
`KERBSIDE_TOKEN_DURATION`.

Two things follow. It operates on `mariadb.get_cluster_config()`, a
raw dict, and never touches the `SFConfig` model — so converting the
fields to `SecretStr` neither improves nor breaks `show-config`. And
the regex is the right tool at the log site, which iterates *all*
fields rather than named ones. Decision 1.

Enumerating the model, exactly three fields carry secrets:
`AUTH_SECRET_SEED`, `MARIADB_PASSWORD` and `LOKI_AUTH_HEADER`
(`config.py:620-626`, documented "Treat as a secret"). The Kerbside
signing key is *not* among them: it lives in a `cluster_config` row
named to end in `_KEY` so the existing regex masks it
(`shakenfist/util/vdi_tokens.py:54-56`), and is not an `SFConfig`
field, so it never reaches the startup log line.

### The comparison trap, verified

`SecretStr('x') == 'x'` is `False`. So `config.AUTH_SECRET_SEED ==
'~~unconfigured~~'` (`config.py:900`) becomes permanently false the
moment the field is wrapped, and `verify_config()` silently stops
refusing to start on an unconfigured seed — converting a loud startup
failure into a cluster that signs tokens with the shipped default.
This is the single most dangerous line in the phase and it is a
one-word diff that looks correct.

## Decisions

1. **The live leak is stopped first, in its own commit, before any
   type change.** `SECRET_CONFIG_KEY_RE` moves from `client/ctl.py` to
   `config.py`, `startup_tasks.py` consults it, and `ctl.py` imports it
   from its new home. This follows the precedent set for
   `handles_credentials()`, which lives in `external_api/base.py`
   specifically so "app.py and base.py cannot disagree about which
   routes are sensitive" (`base.py:82-84`) — ctl and the startup logger
   must likewise not disagree about which config keys are secret.

   This is **the decision most likely to be argued with**, because it
   is redundant with Decision 5: once the three fields are `SecretStr`,
   the log line renders asterisks by itself. Two reasons it is still
   right. The log site loops over every field, so type safety only
   covers the fields somebody remembered to convert, and the next
   plain-`str` secret config option regresses it silently; a name
   regex over the loop covers fields that do not exist yet. And the
   leak is live in production now — it should not wait for a refactor
   that touches the gRPC and SQL layers and cannot land as one small
   reviewable commit.

2. **`PYTHON_TO_SQLALCHEMY` gains `SecretStr: sa.String(255)` in the
   same commit as the field conversion, never after it.** Because the
   fallback only warns, an intermediate commit would produce a tree
   whose fresh-install DDL differs from every deployed cluster's, with
   no version bump to catch it.

3. **No schema version bump and no migration.** Mapping to
   `sa.String(255)` leaves the DDL identical, so
   `NAMESPACE_KEY_ATTRIBUTES_VERSION` stays at 1. A unit test pins the
   generated column type so a later careless mapping change cannot
   drift it.

4. **The legacy `nonced_keys` dict carries `SecretStr`, and `/auth`
   unwraps at the point of use.** The alternative — unwrapping in the
   accessor at `namespace.py:200` and handing `/auth` plain strings —
   is less work and leaves the highest-traffic secret path outside the
   protection, inside an untyped dict. The accessor's own docstring
   says nothing which does not authenticate a request may use it, so
   the dict is internal and its value type is ours to choose.
   `create_token`'s signature becomes `nonce: SecretStr` as a
   consequence, which makes it honest about what it is handed.

   The phase-2 behaviour-preservation tests pin the dict's *shape* —
   which names appear, and that `expiry` is absent for keys that never
   expire. Those assertions are unaffected; only assertions comparing
   the hash or nonce value need `.get_secret_value()`.

5. **All three secret config fields are converted, and
   `verify_config()`'s sentinel comparison is fixed in the same
   commit.** `AUTH_SECRET_SEED`, `MARIADB_PASSWORD` and
   `LOKI_AUTH_HEADER`. The unwrap sites are `config.py:900` (the
   sentinel), `external_api/app.py:66` (`JWT_SECRET_KEY`),
   `mariadb.py:801` (the connection URL) and wherever the Loki header
   is sent. Note `config.py:69` reads
   `os.getenv('SHAKENFIST_MARIADB_PASSWORD')` directly and is
   unaffected — it never sees the model.

6. **The minted plaintext key secret stays a plain `str`.**
   `credentials.generate()` produces the one value in this system that
   is an actual bearer credential rather than a hash of one
   (`util/credentials.py`), and `_namespace_keys_putpost()` must return
   it in the response body because it is never recoverable afterwards
   (`external_api/auth.py:420-425`). Its logging exposure is already
   closed structurally: `/auth` and everything under it is redacted by
   `handles_credentials()` (`base.py:85-87`). Wrapping it would put an
   unwrap in the response serialiser, where a mistake renders
   `**********` into the operator's only copy of the credential — a
   silent destructive failure strictly worse than the exposure it
   would prevent. Recorded in Future work.

7. **Test assertions compare `.get_secret_value()`, and the guard
   tests are proven to still fail.**
   `test_namespace_key_object.py:145-152` asserts the secret appears in
   no event. Post-change `assertNotIn(attrs.key, str(call))` raises
   `TypeError`, and the obvious repair — wrapping the needle in
   `str()` — asserts that `'**********'` is absent, which passes even
   when the real secret is present. That is a test that silently stops
   testing, which is how step 2g's fifth site survived four rounds of
   review. Every such assertion is repaired with
   `.get_secret_value()`, and the phase adds a test that deliberately
   leaks the secret into an event and asserts the guard *fails*.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | medium | sonnet | none | **Stop the live leak.** Move `SECRET_CONFIG_KEY_RE` from `shakenfist/client/ctl.py:152-157` to `shakenfist/config.py` (module level, near the top; it is a compiled regex constant so it is safe above `SFConfig` and does not interact with the import-time `load_cluster_config()` path). Import it back into `ctl.py` so `show_config` is unchanged in behaviour. In `shakenfist/daemons/queues/startup_tasks.py:248-249`, redact matching keys: `value = '<redacted>' if SECRET_CONFIG_KEY_RE.search(key) else value`. Keep the log line otherwise identical — operators grep it. Add a unit test that the startup config log redacts `AUTH_SECRET_SEED` and `MARIADB_PASSWORD` and does not redact `NODE_NAME`. Do not touch any field type in this step. Commit subject: `Redact secret config values from the startup log.` |
| 6b | medium | sonnet | none | **Teach the table generator about `SecretStr`.** In `shakenfist/schema/sqlalchemy.py`, add `SecretStr: sa.String(255)` to `PYTHON_TO_SQLALCHEMY` (`:255-261`) with a comment saying why an explicit entry is required: the fallback at `:365-367` only warns and would silently produce `LONGTEXT`, and since `_ensure_*_schema()` creates tables from the model only when absent, that would diverge fresh installs from upgraded clusters with no version bump. This module is checked with `mypy --strict`, so the dict's declared type `dict[type[Any], sa.types.TypeEngine[Any]]` must still satisfy it. Add unit tests to the sqlalchemy schema tests asserting a `SecretStr` field maps to `String(255)` and that a genuinely unknown type still falls back to `LONGTEXT`. No other file changes. Commit subject: `Map SecretStr to a string column.` |
| 6c | high | opus | worktree | **Convert `key` and `nonce` end to end.** This is deliberately one commit: the field type change breaks every consumer simultaneously, so no smaller split leaves a tree that builds and passes tests. Change `NamespaceKeyAttributesData.key` and `.nonce` to `Annotated[SecretStr, Field(max_length=255)]` (`schema/namespace_key_attributes.py:57-61`), updating the docstring. Then unwrap at each boundary the survey enumerates: the two SQL `.values()` sites (`mariadb.py:12849-12850`, `:12909-12910`) and the two proto converters (`:12973-12975` unwrap, `:13000-13003` rewrap — protobuf string fields reject a `SecretStr`, so mypy on `mariadb.py` will flag a missed one). Make `NamespaceKey.key` / `.nonce` return `Optional[SecretStr]` (`namespace_key.py:264-285`); `hash_secret()` still returns `str` and the model coerces. Per Decision 4 the `Namespace.keys` accessor keeps `SecretStr` in the dict (`namespace.py:200`), so unwrap at the bcrypt compare (`external_api/auth.py:186-189`), change `create_token(nonce: SecretStr)` and unwrap into the claim (`util/access_tokens.py:18,43`), and unwrap the nonce comparison in `verify_token` (`external_api/base.py:730-736`). `Namespace.add_key()` returns the nonce (`namespace.py:261`) as does `NamespaceKey.rotate()` (`namespace_key.py:252`) — decide one return type for both and make the REST callers agree. `namespace.py`, `namespace_key.py` and `external_api/auth.py` are outside mypy's coverage (see `tox.ini:69-108`), so those three files get no type-checker help: read every reference to `.key`/`.nonce`/`nonce` in them rather than relying on the checker. Do not bump any schema version. Commit subject: `Wrap namespace key secret material in SecretStr.` |
| 6d | high | opus | none | **Convert the three config fields.** `AUTH_SECRET_SEED` (`config.py:162`), `MARIADB_PASSWORD` (`:866`) and `LOKI_AUTH_HEADER` (`:620`) become `SecretStr`. Fix `verify_config()`'s sentinel at `:900` to compare `.get_secret_value()` — verified: `SecretStr('x') == 'x'` is `False`, so leaving it makes the "You must configure AUTH_SECRET_SEED!" refusal permanently unreachable. Unwrap at `external_api/app.py:66`, at the connection URL in `mariadb.py:801`, and at the Loki push header (find it in `logship*.py`). Leave `config.py:69` alone: it reads the raw env var, not the model. Check `tools/` and `shakenfist/deploy/` for anything that reads these off the model. Add a unit test that `verify_config()` still fails on the default seed — that is the regression this step is most likely to introduce. Note that 6a already redacts the startup log, so this step should change no log output; if it does, something else was reading a secret. Commit subject: `Wrap secret configuration values in SecretStr.` |
| 6e | medium | sonnet | none | **Harden the guard tests.** Repair every assertion that compares secret material, using `.get_secret_value()` and never `str()` — `test_namespace_key_object.py:145-152` and `:212-217` are the known ones; grep the test tree for others. Add: a test that deliberately passes the plaintext hash into an event and asserts the existing guard *fails* (proving it still tests something); and a round-trip test that a `SecretStr` field survives the model → SQL → model path with its value intact, using the existing `mock_mariadb` harness. Read Decision 7 before starting — the failure mode being defended against is a repair that makes the test pass vacuously. Commit subject: `Prove the secret-material guards still fail.` |
| 6f | medium | sonnet | none | **Sweep and document.** Sweep for other secret-carrying fields still unwrapped: grep the `schema/` models and `config.py` for names matching `SECRET_CONFIG_KEY_RE`, and check the `federation.py` / `trusted_issuer.py` JWKS material. Report findings; convert only what is clearly a secret, and list the rest in the master plan's Future work rather than expanding scope. Then document the convention in `docs/developer_guide/` — a short section stating that secret-carrying model and config fields are `SecretStr`, that unwrapping is explicit and reviewable, and that assertions about secrets compare `.get_secret_value()` because `str()` renders asterisks and passes vacuously. Update `ARCHITECTURE.md` / `AGENTS.md` only if they describe the affected models. Finally set phase 6 to Complete in the master plan's Execution table and in `docs/plans/index.md`. Commit subject: `Document the SecretStr convention for secrets.` |

## Risks and mitigations

* **A missed unwrap persists `**********` as the stored hash or
  nonce.** This is the catastrophic case: the key silently stops
  matching and every derived token stops validating. Mitigated
  structurally — the survey confirmed both persistence paths name
  columns field by field rather than going through `model_dump()`, so
  there is no route by which a dump could substitute the mask — and by
  6e's round-trip test. Checked by the management session reading the
  two `.values()` sites in the 6c diff.
* **A missed unwrap in `create_token` puts the mask in the JWT
  claim.** Fails loud rather than silent: `verify_token` compares the
  claim against the real nonce, so every authenticated request 401s
  and functional CI fails immediately. No extra mitigation needed;
  recorded so the failure is recognised rather than diagnosed from
  scratch.
* **The `verify_config()` sentinel regression.** A cluster would
  start with the shipped default signing seed instead of refusing to.
  Mitigated by naming it in the 6d brief and by the unit test 6d
  adds. The management session must see that test in the diff.
* **Three of the most-affected files have no mypy coverage.**
  `namespace.py`, `namespace_key.py` and `external_api/auth.py` are
  absent from `tox.ini:69-108`, so a missed unwrap there surfaces at
  runtime. Mitigated by the 6c brief instructing an exhaustive manual
  read, and by functional coverage: `/auth` and the federation exchange
  both run in `cluster_ci_tests/test_federation.py`. Adding these files
  to the mypy rollout is a reasonable follow-up but is not this
  phase's job.
* **6a and 6d overlap, and 6d could quietly undo 6a.** If the sub-agent
  doing 6d "simplifies" by removing the redaction as now-redundant, the
  protection for future plain-`str` secrets goes with it. The 6d brief
  says the step should change no log output; the DoD asserts the
  redaction is still present.

## Definition of done

Falsifiable, in order of what would be checked:

- [ ] `grep -n 'Configuration item' shakenfist/daemons/queues/startup_tasks.py`
      shows the value passed through a redaction check, and
      `SECRET_CONFIG_KEY_RE` is defined in `config.py` and imported by
      both `client/ctl.py` and `startup_tasks.py` — defined in exactly
      one place.
- [ ] A unit test fails if the startup config log is reverted to
      logging `AUTH_SECRET_SEED` verbatim.
- [ ] `SecretStr` appears in `PYTHON_TO_SQLALCHEMY`, and a test
      asserts a `SecretStr` model field generates
      `String(length=255)` — not `LONGTEXT`.
- [ ] `NAMESPACE_KEY_ATTRIBUTES_VERSION` is unchanged, and the DDL
      emitted for `namespace_key_attributes` is byte-identical before
      and after the change. Verify by generating `CREATE TABLE` from
      the model on both `develop` and this branch and diffing.
- [ ] `NamespaceKeyAttributesData.key` and `.nonce` are `SecretStr`,
      and `grep -n 'get_secret_value' ` finds an unwrap at each of the
      six sites the survey enumerates and nowhere else in
      non-test code.
- [ ] `verify_config()` still fails on the default
      `~~unconfigured~~` seed, asserted by a test that fails if the
      comparison is left as `== '~~unconfigured~~'`.
- [ ] No assertion about secret material anywhere in the test tree
      compares `str(x)` of a `SecretStr` —
      `grep -rn 'assertNotIn(str(' shakenfist/tests/` finds no secret
      comparison, and the deliberate-leak test from 6e fails when the
      guard it exercises is removed.
- [ ] `pre-commit run --all-files` passes, including the mypy
      invocations for `mariadb.py`, `schema/sqlalchemy.py`,
      `external_api/base.py` and `util/access_tokens.py`.
- [ ] Functional coverage passes: `cluster_ci_tests/test_federation.py`
      and whatever exercises `/auth`, since minting and validating a
      token round-trips every converted field.
- [ ] `docs/plans/order.yml` is unchanged, and phase 6 reads Complete
      in both the master plan's Execution table and
      `docs/plans/index.md`.
- [ ] The developer guide states the convention, including why
      assertions must use `.get_secret_value()`.

## Back brief

Before executing any step, the implementing sub-agent must back brief
the management session on its understanding of the brief and the
surrounding context.

For **6c** specifically the back brief must list, file and line, every
site it intends to unwrap, and state for each whether mypy covers that
file. Agreeing that list before any edit is cheap; discovering a
missed unwrap after the field type has already rippled through the
gRPC and SQL layers is not, and three of the affected files have no
type checker watching them. The management session should compare the
list against the six sites in *What the survey found* and ask about
any difference in either direction.

For **6e** the back brief must state how it will prove each repaired
assertion still fails when the secret genuinely leaks. An assertion
that cannot be made to fail is not a test, and this is the exact
mistake — checking that a named field is gone rather than that the
secret is absent — which let step 2g's fifth site through.

## Review notes

The automated reviewer raised eight action items on the phase 6 PR.
All were addressed; three are worth recording because they changed
something beyond the line they pointed at.

**The name-based redaction covered two of its three sites.** The
reviewer observed that `_config_failure()` in `config.py` dumps every
configuration option with the same shape as the startup banner, and
did not consult `SECRET_CONFIG_KEY_RE` — so the argument this phase
made for keeping the name check (a site which iterates *every* option
needs one, because the types cannot cover an option that does not
exist yet) applied to a site the phase had not touched. The redactor
moved from `daemons/queues/startup_tasks.py` to `config.py` as
`redacted_config_items()` and both callers now use it, which is also
where a general-purpose config helper belongs. `config.dict()` became
`config.model_dump()` while there, dropping a pydantic v1 deprecation.

**The `Optional` nonce.** `Namespace.add_key()` returned
`NamespaceKey.nonce`, an `Optional[SecretStr]` sourced from a fresh
point read, into `create_token(nonce: SecretStr)` which unwraps
immediately. The declared type was a lie and no mypy coverage exists
on any of the three files involved. `NamespaceKey.new()` now records
the nonce it minted on the returned object as `minted_nonce`, set on
all three paths out, and `add_key()` returns that — honestly typed,
and one fewer database read on the key creation path.

**Six vacuous leak guards, not two.** The reviewer noted that
`test_the_secret_guard_detects_a_real_leak()` asserted `Exception`
rather than the assertion failure specifically. Narrowing it to
`self.failureException` prompted re-examining the trap itself, and the
mechanism turned out to be sharper than this plan recorded: `SecretStr`
implements no `__contains__`, so `secret in string` raises `TypeError`,
and `testtools`' `Contains.match()` *catches* `TypeError` and reports
"does not contain". Such an assertion cannot fail, on either operand.

Sweeping for the shape found four more emptied guards beyond the two
this phase had already repaired — in `test_namespace_keys.py` (the
`Namespace.external_view()` redaction guard) and three in
`external_api/test_auth.py` — and not one had failed to announce
itself. Fixing six sites and hoping to notice the seventh is not a
strategy, so `ShakenFistTestCase` now raises `TypeError` when either
operand of `assertIn`/`assertNotIn` is a `SecretStr`. That converts a
silent pass into a loud failure for every test in the suite,
permanently, and it is what found the fourth site. Pinned by
`test_testcase_secret_guard.py`, including that ordinary assertions
are unaffected.

**Operator guidance now exists.** The disclosure was recorded only in
this plan's Future work, which operators do not read. It is now
`docs/operator_guide/credential_rotation.md`, linked from `upgrades.md`
and `logging.md`.
