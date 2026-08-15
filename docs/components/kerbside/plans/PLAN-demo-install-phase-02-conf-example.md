# Phase 2: write the missing `etc/kerbside.conf.example`

Master plan: [PLAN-demo-install.md](/components/kerbside/plans/PLAN-demo-install/)

Planned at **medium** effort. The bulk is mechanical
enumeration of a pydantic model. The judgement calls are
where the file lives given it does not ship in the wheel,
and how honestly to render three defaults that look like
usable values but are not.

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

The mechanism it should demonstrate is real and undocumented
by example. `kerbside/config.py:14-38`'s
`load_ini_settings()` reads `/etc/kerbside/kerbside.ini`
(`INI_PATH`), takes keys from a single `[kerbside]` section
(`INI_SECTION`), upper-cases each key, prefixes it with
`KERBSIDE_`, and sets it in `os.environ` **only if not
already set** — so environment variables win over the INI
file, exactly as `docs/configuration.md:3` claims.

## Scope

In scope:

- `etc/kerbside.conf.example`, covering all 34 fields on
  `Config`.
- Unit tests that stop the file rotting, and that pin the
  INI-to-environment mechanism it demonstrates.
- A comment on issue #131 and a new issue for a bug the
  survey found.

Out of scope, deliberately:

- **Editing `docs/configuration.md` or `ARCHITECTURE.md`.**
  Their pointers become true the moment the file exists.
  See decision 6.
- **Fixing `configuration.md`'s wrong "String (no default)"
  row for `AUTH_SECRET_SEED`.** That belongs with the
  startup guard in issue #131, not here.
- **Adding a startup guard that rejects the sentinel.** That
  is issue #131's fix and a security change of its own.
- **Fixing the `sys.exit()` exit-code bug** found below.

## What the survey found

Verified against the tree at `6946297`. The previous draft
of this plan was written before phase 1 executed; most of it
survived, and the two corrections below are recorded at
their source in the master plan and `index.md` as part of
the planning commit.

### Verified true

- `etc/kerbside.conf.example` does not exist; `etc/` holds
  exactly the two files named above.
- Both dead pointers exist, at the lines given.
- `load_ini_settings()` behaves exactly as described —
  `INI_PATH`, `INI_SECTION`, upper-case, `KERBSIDE_` prefix,
  and `if env_var_name in os.environ` guarding the write, so
  the environment wins.
- Four fields default to the literal `~~unconfigured~~`, at
  `config.py:44,55,68,72`: `AUTH_SECRET_SEED`,
  `KEYSTONE_AUTH_URL`, `KEYSTONE_SERVICE_AUTH_USER`,
  `KEYSTONE_SERVICE_AUTH_PASSWORD`. Exactly four, exactly
  those lines.
- `docs/configuration.md:18` does describe
  `AUTH_SECRET_SEED` as "String (no default)", and issue
  #131 does quote that as part of its evidence (body line
  15). Still open, still no startup guard.
- `openssl rand -hex 32` is at
  `tools/direct-qemu/start-kerbside.sh:63`.
- Tests live in `kerbside/tests/unit/` as `test_*.py` using
  `testtools.TestCase`.

### Correction: the section order was wrong

The previous draft told the implementer to group settings in
`docs/configuration.md`'s order and then gave that order
incorrectly. The real headings, in file order, are:

| Line | Heading |
|------|---------|
| 11 | Basic Settings |
| 21 | TLS Settings |
| 30 | Keystone Settings |
| 45 | Network Settings |
| 57 | Shaken Fist console tokens |
| 68 | Control-plane gRPC service |
| 77 | SPICE firewall |
| 99 | Logging and Monitoring Settings |

Network and Shaken Fist console tokens were transposed, and
two heading names were paraphrased rather than quoted. Use
the table above verbatim.

### Correction: nothing is required, so "required" is a judgement

All 34 fields have defaults —
`Config.model_fields[name].is_required()` is false for every
one. So the live-versus-commented split cannot be derived
from the model, and the previous draft's claim that the
required set was "taken from what `start-kerbside.sh` must
set" is not literally true either: that script sets 13
`KERBSIDE_` variables, not 8. The five extra are lane
artefacts (`API_SOCKET_PATH`, `LOG_OUTPUT_PATH`,
`PROMETHEUS_METRICS_PORT`, `VDI_INSECURE_PORT`) or not a
`Config` field at all (`KERBSIDE_PROXY_BIN`, consumed by the
proxy supervisor). See decision 4 for the basis actually
used.

### New: `etc/` does not ship in the wheel

`pyproject.toml` has no `data_files`, no `package_data` and
no entry covering `etc/`. Phase 1 established the rule —
setuptools_scm's git file-finder contributes tracked files
found *beneath a package directory*, and nothing else — and
`tools/check-wheel.py` now enforces it. So the file these
two documents point at will not exist for anyone who ran
`pip install kerbside`.

This is the same shape as the defect phase 1 fixed, and it
resolves the opposite way. Decision 1.

### New: three defaults look usable and are not

The previous draft flagged one paste-into-production hazard.
There are three:

| Field | Default | Hazard |
|-------|---------|--------|
| `AUTH_SECRET_SEED` | `~~unconfigured~~` | Sentinel; forgeable JWT (#131) |
| `SQL_URL` | `mysql://kerbside:QwwMH-4w@kolla/kerbside` | Embeds a plausible password, and a hostname meaningful in one deployment only |
| `PUBLIC_FQDN` | `kerbside.home.stillhq.com` | A personal hostname |

`SQL_URL`'s default is the interesting one: it reads as a
working DSN, so reproducing it verbatim in an example file
publishes a credential-shaped string that someone will keep.
Decision 3.

### New: phase 1 left a constant worth reusing

`kerbside/main.py:377` now defines
`_UNCONFIGURED = '~~unconfigured~~'` and `main.py:453`
refuses to mint a demo token while `AUTH_SECRET_SEED` still
holds it. So the sentinel is now rejected at *mint* time
while still accepted at *startup*. Whoever fixes #131 has a
constant to promote rather than a literal to duplicate; step
2d says so on the issue.

### New bug, not fixed here

`load_ini_settings()` calls a bare `sys.exit()` when
`configparser` raises (`config.py:37-38`). Bare `sys.exit()`
exits **zero**, so a malformed `/etc/kerbside/kerbside.ini`
terminates the daemon while reporting success to systemd or
a container supervisor, which will neither restart it nor
flag a failure. Found while verifying the INI path;
unrelated to writing an example file, so step 2e files it
rather than fixing it.

Filed as **#313**. Implementation found it is easier to hit
than first thought: `load_ini_settings()` uses a default
`ConfigParser`, so interpolation is active on INI *values*
too, and a lone `%` raises `InterpolationSyntaxError` —
which is a `configparser.Error` subclass and lands in this
handler. A percent-encoded database password is the ordinary
way to reach it, so the silent exit-zero is a likely first
experience rather than an exotic one. This is the INI-file
half of the same trap phase 1 fixed on the alembic side, and
it is the reason the example file documents `%%`.

## Decisions

1. **The example stays at `etc/kerbside.conf.example`, and
   is documentation rather than a shipped artifact.** It
   will not be in the wheel, and that is acceptable because
   an example config is *read by a human* whereas the
   migrations phase 1 packaged are *executed by the
   installed code*. `kerbside db upgrade` cannot work
   without its migrations at runtime; a human can read this
   file on the docs site or in the repository. Packaging it
   would also create two copies to keep in agreement, or a
   third test to prove they agree.

   **This is the decision most likely to be argued with.**
   The counter-case is symmetry: phase 1 packaged the
   migrations for exactly the "a wheel install cannot see
   it" reason being waved away here, and an operator reading
   `--help` has no path to the file. If a reviewer prefers
   the symmetric answer, the cheap form is a
   `kerbside config example` subcommand printing a packaged
   copy — a phase of its own, not a step here. Recorded
   under Future work either way.

2. **Group by `docs/configuration.md`'s real section order**,
   using the eight headings quoted verbatim in the
   correction above, so the two files can be read side by
   side.

3. **Three placeholder rules, not two.** No line in the file
   may contain a value that would function if pasted.
   `AUTH_SECRET_SEED` gets an obvious placeholder plus the
   `openssl rand -hex 32` hint; `SQL_URL` and `PUBLIC_FQDN`
   get placeholders with their real defaults *described in
   prose* in the adjacent comment rather than reproduced as
   values. The anti-rot test checks key presence, not value
   fidelity, so a placeholder satisfies it — and step 2b
   asserts the three real values are absent, so this rule is
   enforced rather than requested.

   **Corrected in review: the seed is the exception, and this
   decision had it exactly backwards.** The rule as written
   produced `auth_secret_seed =
   CHANGEME-generate-with-openssl-rand-hex-32`, on the
   reasoning that shipping the sentinel would be a working
   configuration signing with a public constant. It is the
   other way round. *Any* value the operator forgets to
   replace signs with a constant published in this tree; what
   distinguishes the sentinel is that it is the only such
   value the code can *recognise* — `main._UNCONFIGURED`,
   checked before minting a demo token (the "constant worth
   reusing" finding above). A placeholder of our own
   invention is a public constant that is also undetectable,
   so it is strictly worse than what an operator would have
   had before this file existed: it defeats the one guard
   phase 1 added. The file now ships `auth_secret_seed =
   ~~unconfigured~~`, the test asserts equality with
   `main._UNCONFIGURED` rather than inequality, and the
   comment explains why a friendlier-looking placeholder
   would be a downgrade. Demonstrated to fail: restoring the
   old placeholder yields `'~~unconfigured~~' !=
   'CHANGEME-generate-with-openssl-rand-hex-32'`.

   `SQL_URL` and `PUBLIC_FQDN` are unaffected — neither
   default is a value any code can detect, so for those the
   original rule stands. The two rules are reconcilable:
   "nothing that would function if pasted" is the goal, and
   for the seed the sentinel is the value that most reliably
   *fails*.

4. **The live-key set is chosen on "has no default that
   could work anywhere", not on what the CI lane exports.**
   That yields the same eight keys the previous draft
   listed — `sql_url`, `auth_secret_seed`, `sources_path`,
   `public_fqdn`, `cacert_path`, `proxy_host_cert_path`,
   `proxy_host_cert_key_path`, `proxy_host_subject` — but
   for a stated reason: each is a sentinel, a credential, a
   hostname, or a path into a PKI that is necessarily local.
   Everything else is commented out at its real default.

5. **Defaults are commented in the exact form
   `# key = value`, single leading `# `.** The anti-rot test
   recognises documented-but-defaulted keys by that pattern,
   so the formatting is load-bearing. The file says so in
   its header, and the test's docstring names the file.

6. **Neither `docs/configuration.md` nor `ARCHITECTURE.md`
   is edited.** Their pointers become true when the file
   appears, which is the whole point, and leaving
   `configuration.md`'s wrong `AUTH_SECRET_SEED` row in
   place keeps issue #131's evidence intact for whoever
   fixes it. Step 2d records the divergence on the issue so
   the tree and the issue do not drift silently.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | medium | sonnet | none | Write `etc/kerbside.conf.example`: one `[kerbside]` section, keys lower-case (they are upper-cased on load, so this also demonstrates the case handling), grouped under comment headers quoting the eight headings in the "section order was wrong" table verbatim and in that order. Enumerate fields from `kerbside/config.py` — all 34 on `Config`, none omitted — basing each one-line comment on that field's `Field(description=...)`. Keep `API_SOCKET_PATH`'s SUN_LEN warning; it is the one long description worth preserving. The eight keys in decision 4 are live with placeholders; the other 26 are commented out at their real default in the exact form `# key = value` (decision 5). Obey decision 3: no pasteable value for `auth_secret_seed`, `sql_url` or `public_fqdn`, real defaults described in prose instead. The header comment block must state the INI path `/etc/kerbside/kerbside.ini`, the single `[kerbside]` section, that environment variables override the file, and that the `# key = value` form is asserted by a test. |
| 2b | medium | sonnet | none | Add the anti-rot test to `kerbside/tests/unit/`, matching neighbouring style (`testtools.TestCase`, docstring naming the defect it guards). Parse `etc/kerbside.conf.example`, treating both live keys and `# key = value` comments as covered. Assert: every name in `Config.model_fields` appears lower-cased; no key in the file is absent from `model_fields`, so a rename leaves no orphan; the file parses under `configparser`; and the three real values from decision 3 — the literal `QwwMH-4w`, `kerbside.home.stillhq.com`, and any 64-character hex run — do not appear. Locate `etc/` by walking up from `__file__` and raise `unittest.SkipTest` naming why when absent, since an installed wheel has no `etc/`. Then prove the test can fail: add a throwaway field to `Config`, confirm a failure naming it, remove it, and report that you did so. |
| 2c | medium | sonnet | none | Add a second test pinning the mechanism the example demonstrates, which nothing currently covers: monkeypatch `kerbside.config.INI_PATH` to the example file, call `load_ini_settings()` with a clean `os.environ`, and assert the keys arrive as `KERBSIDE_`-prefixed upper-cased variables. Then pre-set one of them to a different value, call again, and assert it is **not** overridden — the precedence `docs/configuration.md:3` promises and no test enforces. Restore `os.environ` and `INI_PATH` with `addCleanup`. |
| 2d | low | haiku | none | Comment on GitHub issue #131. Record three things: `docs/configuration.md:18` still describes `AUTH_SECRET_SEED` as "String (no default)" and is still wrong; `etc/kerbside.conf.example` now documents the sentinel default accurately, so the tree and the issue no longer disagree in the same direction; and phase 1 added `_UNCONFIGURED` at `kerbside/main.py:377` plus a mint-time refusal at `main.py:453`, so a startup guard should promote that constant rather than duplicate the literal. State that the `configuration.md` table was deliberately left alone so the fix lands with the guard. Do not close or otherwise modify the issue. |
| 2e | low | haiku | none | File a new issue for the bug in "New bug, not fixed here": `kerbside/config.py:37-38` calls a bare `sys.exit()` on `configparser.Error`, which exits zero, so a malformed `/etc/kerbside/kerbside.ini` stops the daemon while reporting success to its supervisor. Include the file and line, why exit zero is the defect rather than the exit itself, and that phase 2 found it while writing the example config but left it alone as unrelated to documentation. Suggest `sys.exit(1)`. |

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| The example silently falls behind `config.py` | Step 2b's coverage test, demonstrated to fail before being trusted. The demonstration is the mitigation; an untested coverage test is decoration. |
| Someone pastes a placeholder into production | Decision 3, enforced by assertions in 2b rather than by a comment asking nicely. |
| The anti-rot test skips instead of running, so coverage is imaginary | It skips only when `etc/` is absent, which never holds in CI — `sanity_checks` runs from a checkout. Reviewer check: confirm the CI log shows the test running, not skipping. |
| The file and `configuration.md` drift apart in wording | Out of scope to unify here; phase 5 cross-links them. Recorded so phase 5 need not rediscover it. |
| Decision 1 is wrong and the file should ship | Cheap to reverse: the file is data, and a `kerbside config example` command could serve a packaged copy later. Nothing in this phase forecloses it. |

## Definition of done

Each item is checkable by running something. Outcome recorded
after each.

- [x] `etc/kerbside.conf.example` exists.
- [x] Every one of the 34 `Config` fields appears in it —
      8 live, 26 commented, 34 documented, nothing missing.
- [x] No orphan keys — the converse assertion in 2b passes.
- [x] The 2b test has been *demonstrated to fail*. Adding a
      throwaway field yields `[] != ['throwaway_canary']:
      these settings exist on Config but are absent from
      etc/kerbside.conf.example: throwaway_canary`, and an
      unknown key yields `[] != ['renamed_away_setting']:
      etc/kerbside.conf.example documents keys that are not
      fields on Config`. Both reverted.
- [x] No pasteable value: the grep returns 0 and no
      64-character hex run appears. Implemented more
      strongly than planned — the forbidden values are read
      from `Config.model_fields` rather than hardcoded, so
      changing a default in `config.py` cannot defeat the
      guard.
- [x] The 2c test proves an existing environment variable
      survives `load_ini_settings()`, and fails with
      `'preset.example.net' != 'kerbside.example.com'` when
      the guard is replaced by `if False`.
- [x] `git diff --stat develop -- docs/configuration.md
      ARCHITECTURE.md` is empty.
- [x] Dropping the file at `/etc/kerbside/kerbside.ini`
      configures kerbside — verified in a `python:3.13-slim`
      container, installing the example at the real path
      with the eight live keys edited as an operator would,
      with no monkeypatching: the live keys arrive, the
      commented defaults resolve to their defaults
      (`API_GRPC_WORKERS` 8, `KEYSTONE_ACCESS_GROUP`
      kerbside), and `KERBSIDE_PUBLIC_FQDN` in the
      environment still wins.

      **Partial, and deliberately so.** This proves
      configuration *loading* from the documented path, not
      a full daemon start, which additionally needs a
      database and issued TLS material. That is phase 3's
      deliverable, and standing it up here would have
      duplicated it. A container was used because `sudo`
      requires a password on this host and writing to the
      host's `/etc` for a test is not worth it.
- [x] `pre-commit run --all-files` passes; unit tests went
      from 156 to **167** (11 added).
- [x] A comment exists on #131
      (`issuecomment-5298199481`), and the `sys.exit()` bug
      is filed as **#313**.

### Added in review

The automated review raised 11 items against the first push;
all were addressed. Two changed behaviour rather than prose:

- [x] **The shipped seed is a value the code can recognise.**
      Decision 3's correction above. `auth_secret_seed` is
      now `~~unconfigured~~` and
      `test_the_live_seed_is_one_the_guard_recognises`
      asserts equality with `main._UNCONFIGURED`, imported
      rather than restated so the guard and the example
      cannot drift. Demonstrated to fail: restoring the old
      placeholder yields `'~~unconfigured~~' !=
      'CHANGEME-generate-with-openssl-rand-hex-32'`.
- [x] **The 26 commented values are pinned to the real
      defaults**, not merely their key names, so the header's
      promise that they show "that setting's real default"
      is now held rather than asserted. Demonstrated to fail:
      changing `API_GRPC_WORKERS` from 8 to 16 in
      `config.py` yields `[] != ["api_grpc_workers documents
      '8' but the default is '16'"]`. This goes beyond
      decision 3's "key presence, not value fidelity", which
      was the wrong line to draw — a wrong default is worse
      than a missing one, because the reader will act on it.

The rest were prose and test-legibility: 0600 ownership
guidance in the header (the file holds a signing key and a
cleartext password), a header rule that no longer contradicts
the TLS section, a comment recording *why* the four TLS keys
are live at their defaults, failure messages that state the
formatting contract and the live/commented criterion, the
interpolation probe bound to an assertion so it cannot be
tidied away as dead code, and the precedence test reading its
expected value from the file rather than pinning a
placeholder's text. `AGENTS.md`'s configuration section was
corrected too: it documented a `KERBSIDE_CONFIG_PATH`
environment variable that does not exist and a
`/etc/kerbside/kerbside.conf` path that is not the one
kerbside reads. Decision 6 protects `docs/configuration.md`
and `ARCHITECTURE.md` to keep #131's evidence intact; it does
not extend to a plainly false statement in `AGENTS.md`. The
`.conf` versus `.ini` filename mismatch is recorded as a
candidate rename in the phase 5 plan, where both files that
name it are already being edited.

## Future work

- **`kerbside config example`** — a subcommand printing a
  packaged copy of the example, which would make decision 1
  moot for wheel installs. Not needed until someone asks;
  recorded because decision 1 is the arguable one.
- **`configuration.md`'s `AUTH_SECRET_SEED` row** is wrong
  and stays wrong until #131 is fixed, on purpose.
- **The `sys.exit()` exit-code bug**, filed as #313. Worth
  pairing with a check that the INI file parses at all,
  since the percent-interpolation cause is common enough to
  be someone's first experience of kerbside.
- **Unifying `configuration.md` with the example file** so
  one is generated from the other. Today both are written by
  hand from `config.py`, which is two places to rot instead
  of one.

## Back brief

Before executing any step, back brief me on your
understanding of this plan and how the work you intend to do
aligns with it.

Gate: **do not start 2a until decision 1 is confirmed.**
Writing the file is cheap, but writing it in the wrong place
means moving it, updating two documents this phase promises
not to touch, and re-pointing the test. That is the one
choice here that is expensive to reverse after the fact.

## Registration note

The master plan's phase 2 section and the `index.md` phase 2
entry were corrected as part of the planning commit: the
transposed section order and the "required set comes from
`start-kerbside.sh`" claim were both wrong at their source,
and the `etc/`-does-not-ship finding was absent. A later
step need not redo this.
