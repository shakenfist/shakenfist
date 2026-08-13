# Phase 2: write the missing `etc/kerbside.conf.example`

Master plan: [PLAN-demo-install.md](/components/kerbside/plans/PLAN-demo-install/)

Planned at medium effort: mechanical enumeration of a
pydantic model, with one judgement call about how much to
say per setting.

## Situation

Two documents refer the reader to a file that does not
exist:

- `docs/configuration.md:5` — "See `etc/kerbside.conf.example`
  for a complete configuration example."
- `ARCHITECTURE.md:345` — "See `etc/kerbside.conf.example`
  for a complete configuration reference."

`etc/` contains only `example-static-sources.yaml` and
`kolla-ci-globals-overlay.yml`. The reference has been dead
long enough that two documents accumulated it.

The configuration mechanism it should demonstrate is real
and undocumented by example:
`kerbside/config.py:14-38`'s `load_ini_settings()` reads
`/etc/kerbside/kerbside.ini` (`INI_PATH`), takes keys from a
single `[kerbside]` section (`INI_SECTION`), upper-cases each
key, prefixes it with `KERBSIDE_`, and sets it in
`os.environ` **only if not already set** — so environment
variables win over the INI file, as `docs/configuration.md:3`
says.

## Mission

`etc/kerbside.conf.example` exists, is a valid INI file that
`load_ini_settings()` would parse, covers every field on
`Config`, and cannot silently fall behind `config.py` because
a test fails when it does.

## Approach

### Shape

One `[kerbside]` section, keys in lower case (they are
upper-cased on load, so lower case reads naturally and
proves the case-insensitivity), grouped with comment headers
matching `docs/configuration.md`'s section order so the two
can be read side by side: Basic, TLS, Keystone, Shaken Fist
console tokens, Network, gRPC control plane, SPICE firewall,
Logging and monitoring.

Every setting gets a one-line comment. Settings that are
required in practice are **uncommented with a placeholder**;
settings that have a working default are **commented out
showing that default**. This makes the file a template rather
than a wall of redundant assignments: uncomment what you
need to change.

The required-in-practice set, taken from what
`tools/direct-qemu/start-kerbside.sh` must set for kerbside
to function: `sql_url`, `auth_secret_seed`, `sources_path`,
`public_fqdn`, `cacert_path`, `proxy_host_cert_path`,
`proxy_host_cert_key_path`, `proxy_host_subject`.

### Two accuracy traps

1. **Do not describe the sentinel defaults as absent.**
   `AUTH_SECRET_SEED`, `KEYSTONE_AUTH_URL`,
   `KEYSTONE_SERVICE_AUTH_USER`, and
   `KEYSTONE_SERVICE_AUTH_PASSWORD` all default to the
   literal string `~~unconfigured~~`
   (`config.py:44,55,68,72`). `docs/configuration.md`
   currently calls `AUTH_SECRET_SEED` "String (no default)",
   which is wrong, and **issue #131 quotes that wrongness as
   part of its evidence**. So: state the truth in
   `kerbside.conf.example` (there is a sentinel default and
   nothing rejects it at startup), and do **not** edit
   `docs/configuration.md`'s table in this phase. Record the
   discrepancy as a comment on issue #131 instead, so the
   issue and the tree do not diverge under the person fixing
   it. Phase 5 may link to the example file from
   `configuration.md`, which is a different change.

2. **`auth_secret_seed` must not ship a plausible-looking
   value.** An example file containing a real-looking hex
   string is a value someone will paste into production. Use
   an obvious placeholder and give the command that
   generates a real one: `openssl rand -hex 32`, matching
   `start-kerbside.sh:63`.

### The anti-rot test

Add a test in `kerbside/tests/` that:

1. Parses `etc/kerbside.conf.example` with `configparser`,
   reading **both** live and commented-out keys — the
   commented ones are the documented defaults and must count
   as covered. Recognise a commented key by the pattern
   `# key = value` at the start of a line, which means the
   file must use exactly that form for defaults, with a
   single leading `# `. Say so in a comment at the top of
   the example file, because the test now depends on its
   formatting.
2. Enumerates `Config.model_fields` and asserts every field
   name, lower-cased, is present.
3. Asserts the converse: every key in the file corresponds
   to a real field, so a renamed setting leaves no orphan.

Locating the file from the test: it is repository data, not
package data, so `importlib.resources` does not apply. Walk
up from `__file__` to the directory containing `etc/`, and
**skip the test if not found** rather than failing — an
installed wheel has no `etc/`, and a test that fails when
run from site-packages is a worse outcome than one that
skips. Use the same skip idiom as any existing repo-data
test in `kerbside/tests/`; if there is none,
`unittest.SkipTest` with a message naming why.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | medium | sonnet | none | Write `etc/kerbside.conf.example` per the "Shape" section above. Enumerate the fields from `kerbside/config.py` — every field on `Config`, in the section grouping given, with the `description=` text from each `Field()` as the basis of its comment (condensed to one or two lines; several descriptions in `config.py` are long, e.g. `API_SOCKET_PATH`'s SUN_LEN warning, which is worth keeping). Required-in-practice keys live; defaulted keys commented out in the exact form `# key = value`. Obey both accuracy traps: sentinel defaults described honestly, `auth_secret_seed` a visible placeholder with the `openssl rand -hex 32` hint. Add a header comment block explaining the INI path (`/etc/kerbside/kerbside.ini`), the single `[kerbside]` section, that env vars override the file, and that the `# key = value` form is asserted by a test so defaults must keep that shape. |
| 2b | medium | sonnet | none | Add the anti-rot test described in "The anti-rot test" to `kerbside/tests/`, matching the existing test style in that directory. All three assertions: every `Config` field covered, no orphan keys, and the file parses. Skip cleanly when `etc/` is absent. Run it, then deliberately add a throwaway field to `Config` and confirm the test fails, then remove it — report that you did this, because a coverage test that cannot fail is worthless. |
| 2c | low | haiku | none | Post a comment on GitHub issue #131 recording that `docs/configuration.md`'s "String (no default)" description of `AUTH_SECRET_SEED` is still present and still wrong, that `etc/kerbside.conf.example` now documents the sentinel default accurately, and that the `configuration.md` table was deliberately left alone so the fix lands with the startup guard. Do not close or otherwise modify the issue. |

Note: no step here edits `docs/configuration.md` or
`ARCHITECTURE.md`. Their pointers become true the moment the
file exists, which is the point. Phase 5 revisits
`configuration.md` for cross-linking.

## Success criteria

* `etc/kerbside.conf.example` exists and every field on
  `Config` appears in it.
* The test from 2b passes, and has been demonstrated to fail
  when a field is added without updating the file.
* Dropping the file at `/etc/kerbside/kerbside.ini` with the
  required keys filled in is sufficient to configure
  kerbside — verified by doing it, not by inspection.
* `docs/configuration.md:5` and `ARCHITECTURE.md:345` now
  point at something real, with no edit to either.
* `tox -eflake8` and `tox -epy3` pass.
