# Phase 1: Default structured JSON logging and the field-name contract

## Context

This is phase 1 of
[`PLAN-remove-syslog-forwarding.md`](PLAN-remove-syslog-forwarding.md).
It is the **producer-side** phase: it makes the
`shakenfist_utilities` library emit structured JSON as the only
daemon log format, cleans the JSON record so it is fit to index,
documents the field-name contract decided in phase 0, gives the
`logs` module the unit tests it currently lacks, cuts a new
library release, and bumps the pin in `shakenfist`.

It does **not** add any Loki transport. The on-disk spool,
drainer, and HTTP push all live in `shakenfist` and are built in
phase 2. The library never imports `shakenfist` and gains no new
runtime dependency.

**This phase spans two repositories plus a credentialed
release**, so it cannot be done as a single in-tree sub-agent
run:

- The library lives in the **sibling repo** at
  `/srv/kasm_profiles/mikal/vscode/src/shakenfist/library-utilities`
  (remote `github.com/shakenfist/library-utilities`, default
  branch `develop`, versioned by `setuptools_scm` from `v*` git
  tags; latest tag `v0.8.4`). It is **not** a git worktree of
  this repo — sub-agents must operate in that checkout.
- The pin bump is in **this** repo
  (`shakenfist/pyproject.toml:27`).
- The release (signed tag + PyPI upload via `release.sh`) and
  the library PR are **operator/management actions** — they need
  credentials and, per the operator's git rules, the operator
  creates all PRs. Sub-agents prepare the branch; they do not
  tag, publish, or open PRs.

**Supersedes an existing draft.** The library repo already
contains `library-utilities/docs/plans/PLAN-loki.md`, a draft
proposing a *library-side* `LokiHandler` with an *in-memory*
`queue.Queue` and a new `requests` dependency. The operator has
decided to **supersede** it: the master plan's on-disk spool in
`shakenfist` is the chosen architecture. Phase 1 replaces that
draft's content with a short "superseded" pointer so the two
plans do not contradict each other.

## Key references in the existing code

- `library-utilities/shakenfist_utilities/logs.py`:
  - `setup(name, syslog=True, json=False, logpath=None)` at
    `:178` — handler/formatter selection; the `JsonFormatter`
    branch (`:209-222`) with `enabled_fields`; the
    `TextFormatter` branches to remove (`:223-231`); the
    `SHAKENFIST_LOG_TO_STDOUT` test branch (`:198-201`).
  - `SyslogAdapter.process()` at `:110-114` — **prepends**
    `setproctitle[pid:thread] ` to every message; in JSON mode
    this pollutes the `message` field (the wart phase 1 fixes).
  - `SyslogAdapter._normalize()` `:78-80` (lower-cases keys) and
    `with_fields()` `:88-104` (unwraps objects to `.uuid`, uuid
    → str); the flask `request-id` injection `:69-71`.
  - `setup_console()` `:237-248`, `ConsoleLogger` `:117`,
    `ConsoleAdapter` `:137-144`, `ConsoleLogFormatter` `:147-162`
    — the human-readable CLI path that **stays unchanged**.
- `library-utilities/pyproject.toml` — `dependencies` (no
  `requests`; phase 1 keeps it that way), the `setuptools_scm`
  config, the `test` extra (`coverage`, `testtools`, `mock`,
  `stestr`, `tox`, `flake8`).
- `library-utilities/tox.ini` — `stestr run` testenv and the
  `flake8` testenv (style on `-HEAD`).
- `library-utilities/test.py` — the ad-hoc smoke script that is
  the *only* current coverage; phase 1 turns it into real tests
  under `shakenfist_utilities/tests/`.
- `library-utilities/release.sh` — the interactive release tool
  (prompts for version, signs+pushes `v<version>`, `python -m
  build --wheel`, `twine upload`).
- `library-utilities/docs/plans/PLAN-loki.md` — the draft to
  supersede.
- `pyproject.toml:27` (this repo) — `shakenfist-utilities==0.8.4`,
  the pin to bump.
- Phase-0 Decisions in the master plan — the field-name contract
  (`{job,daemon,host}` labels are added later by the *shakenfist*
  handler, not the library; the library emits the base JSON
  fields + `with_fields` body) and the JSON-only mechanism.

## JSON-only mechanism (refinement of phase-0 D4)

Phase-0 D4 noted all 103 `logs.setup(...)` call sites in
`shakenfist/` use the defaults today and offered two paths to
JSON-only: flip the library default, or have SF pass `json=True`
at every site. With the library draft superseded and a breaking
library release now in scope, the **cleaner choice is to make
JSON the daemon default in the new release**, so SF's 103 call
sites need no edits:

**Decision:** In the new library release (shipped as `v0.8.5`, a
breaking change carried pragmatically in a 0.8.x patch bump),
`setup()` installs the `JsonFormatter` on the
syslog/file/stdout handler **unconditionally** for the daemon
path. The `TextFormatter` import and its branches (`:223-231`)
are removed. The `json` parameter is retained in the signature
for source-compatibility but is documented as deprecated /
no-op (always JSON now), so existing callers that pass
`json=True`/`json=False` keep working without a `TypeError`.
`setup_console()` and the `SHAKENFIST_LOG_TO_STDOUT` test branch
are untouched. Because JSON becomes unconditional, every SF
`logs.setup(__name__)` call already gets JSON — **no SF logging
call sites change in this phase** (only the pin bumps).

Non-SF consumers who upgrade to `0.8.5` and relied on text output
get JSON; that is a breaking change, shipped pragmatically as a
0.8.x patch bump and called out in the release notes. Consumers who need text
stay on `0.8`.

## Deliverables

Phase 1 is complete when:

1. **(library) JSON-only + clean record.** `setup()` emits JSON
   unconditionally for daemons; the message field is clean (no
   `setproctitle[pid:thread]` prefix); process/thread identity
   is carried as **structured fields** instead. `setup_console()`
   unchanged.
2. **(library) Field-name contract documented.** A docs page
   enumerating the JSON field names the library emits (base
   fields + the `with_fields` conventions: lower-cased keys,
   object→`.uuid`, the flask `request-id`), noting that
   `{job,daemon,host}` *labels* are applied downstream by
   `shakenfist`, not the library. Referenced from the library
   `README.md`.
3. **(library) Unit tests.** Real `stestr` tests under
   `shakenfist_utilities/tests/` covering the JSON output shape,
   the clean message, `with_fields` behaviour, and that
   `setup_console()` stays human-readable. `test.py` retired or
   folded in.
4. **(library) Draft superseded.**
   `library-utilities/docs/plans/PLAN-loki.md` replaced with a
   short superseded-by pointer to this master plan.
5. **(release) `v0.8.5` published to PyPI** via `release.sh`
   (operator/management action).
6. **(shakenfist) Pin bumped** `pyproject.toml:27` to
   `shakenfist-utilities==0.8.5`, landed **after** the release
   is on PyPI so CI can install it.
7. `pre-commit run --all-files` passes here; the library's `tox`
   (flake8 + stestr) passes in the library repo.

## Step items

### 1a — Library: JSON-only and a clean JSON record

In the library checkout, on a feature branch:

- Make `JsonFormatter` unconditional for the daemon
  syslog/file/stdout handlers; remove the `TextFormatter` import
  and the `:223-231` branches. Keep the `json` parameter in
  `setup()`'s signature (deprecated/no-op) so callers that pass
  it do not break.
- Fix `SyslogAdapter.process()` (`:110-114`): stop prepending
  `setproctitle()[pid:thread]` to the message. The `message`
  field must contain only the caller's message. Instead surface
  process/thread identity as **structured JSON fields** — add
  `process` (pid) and `thread_name` (already present via
  `enabled_fields`) and, if useful, the process title — via
  `JsonFormatter.enabled_fields` mapping record attributes
  (`record.process`, `record.threadName`) and/or an injected
  field. Confirm `ConsoleAdapter.process()` (`:138-144`) is
  *not* affected (CLI output keeps its human format).
- Verify with a quick local run that a daemon log line is now a
  single clean JSON object with `message` unpolluted and pid
  present.

Recommended effort **high** (opus): the adapter/formatter
interaction and backward-compat of the `json` param are subtle,
and getting the clean-message change wrong silently corrupts
every log line.

### 1b — Library: document the field-name contract

Add a docs page (e.g. `library-utilities/docs/log-record-fields.md`)
listing the JSON field names the library emits: the base fields
from `enabled_fields` (`logger_name`, `ts`, `level`,
`thread_name`, `message`, `exception_class`, `stack_trace`,
`module`, `function`, plus the new `process`/pid) and the
`with_fields` conventions (keys lower-cased, objects unwrapped
to their `.uuid` string, the flask `request-id`). State
explicitly that the Loki stream **labels** `{job, daemon, host}`
are applied by the `shakenfist` shipper downstream and are **not**
the library's concern, and that high-cardinality identifiers stay
in the body. Link it from the library `README.md`.

Recommended effort **low–medium** (sonnet); it transcribes the
phase-0 contract.

### 1c — Library: unit tests for the logs module

Add `stestr` tests under
`library-utilities/shakenfist_utilities/tests/` (the `test`
extra already provides `testtools`/`stestr`/`mock`). Cover:

- `setup()` output is valid JSON and contains the expected base
  fields.
- The `message` field is clean — no `setproctitle[pid:thread]`
  prefix — and `process`/pid is present as a field.
- `with_fields({...})` adds fields, lower-cases keys, and
  unwraps an object with a `.uuid` and a `uuid.UUID` to strings.
- Passing `json=False` (deprecated) still yields JSON (no
  `TypeError`, no text path).
- `setup_console()` produces human-readable, non-JSON output
  (assert it is not JSON / contains the colorised level form).

Retire `test.py` (fold its two smoke cases into the suite).
Ensure `tox` (stestr) runs green in the library repo.

Recommended effort **medium** (sonnet) with a detailed brief.

### 1d — Library: supersede the draft plan

Replace the body of `library-utilities/docs/plans/PLAN-loki.md`
with a brief note: "Superseded by
`shakenfist/docs/plans/PLAN-remove-syslog-forwarding.md` — the
Loki shipper (on-disk spool + drainer + HTTP push) lives in the
`shakenfist` package, not this library; the library's role is
structured-JSON formatting and the field-name contract only."
Keep the file as a breadcrumb rather than deleting it.

Recommended effort **low** (haiku).

### 1e — Release `v0.8.5` (operator/management action)

After 1a–1d are reviewed and merged via an operator-created PR:
run `library-utilities/release.sh`, choosing version `0.8.5`
(tags+pushes `v0.8.5`, builds the wheel, `twine upload` to
PyPI). This needs PyPI/signing credentials and is **not** a
sub-agent step. Include a one-line release note that JSON is now
the only daemon format (breaking for text consumers).

### 1f — shakenfist: bump the pin (this repo)

After `v0.8.5` is live on PyPI, change `pyproject.toml:27`
`shakenfist-utilities==0.8.4` → `==0.8.5`, in a single small
commit in this repo. CI then validates that SF still installs
and that all daemons log JSON. **Ordering is load-bearing:**
bumping before the release is on PyPI breaks CI's install.

Recommended effort **low** (haiku/sonnet).

### Deferred to phase 2

The redundant `fqdn` field in `shakenfist/eventlog.py:82,105`
(duplicates the `host` label value) is a *shakenfist*-side
producer change, not a library change. Fix it in phase 2
alongside the other `shakenfist` code rather than mixing a
shakenfist edit into this library-focused phase. Noted here so
it is not lost.

## Step-level guidance

Note: 1a–1d run in the **library checkout** (a separate repo, on
a feature branch — *not* an `isolation: worktree` of this repo).
1f runs in this repo.

| Step | Effort | Model | Where | Brief for sub-agent |
|------|--------|-------|-------|---------------------|
| 1a | high | opus | library branch | Make `JsonFormatter` unconditional in `logs.py setup()` (remove `TextFormatter` import + `:223-231`; keep `json` param as deprecated no-op). Remove the `setproctitle[pid:thread]` prefix from `SyslogAdapter.process()` (`:110-114`); surface pid via `JsonFormatter.enabled_fields` (`record.process`) so `message` is clean. Leave `ConsoleAdapter`/`setup_console` untouched. Verify a daemon line is one clean JSON object. |
| 1b | low–medium | sonnet | library branch | Add `docs/log-record-fields.md` enumerating emitted JSON fields + `with_fields` conventions (lower-case, `.uuid` unwrap, flask `request-id`); state that `{job,daemon,host}` labels are applied by the shakenfist shipper, not the library; link from `README.md`. |
| 1c | medium | sonnet | library branch | Add `stestr` tests under `shakenfist_utilities/tests/`: JSON validity + base fields; clean message + pid field; `with_fields` lower-case/uuid-unwrap; `json=False` still JSON; `setup_console` is human-readable. Retire `test.py`. Make `tox` green. |
| 1d | low | haiku | library branch | Replace `docs/plans/PLAN-loki.md` body with a superseded-by pointer to the shakenfist master plan (architecture lives in shakenfist; library is format-only). |
| 1e | — | — | operator | Operator creates the library PR, merges, runs `release.sh` to publish `v0.8.5` to PyPI with a "JSON-only, breaking for text consumers" note. Not a sub-agent step. |
| 1f | low | sonnet | this repo | After `v0.8.5` is on PyPI, bump `pyproject.toml:27` to `shakenfist-utilities==0.8.5`; commit. |

## Step ordering and dependencies

- 1a → 1c (tests assert 1a's behaviour) and 1a → 1b (the doc
  describes the post-1a fields). 1d is independent.
- 1a–1d land together in one library PR (operator-created).
- 1e (release) follows the merge; **1f (pin bump) must follow
  1e** — CI in this repo installs the pinned version from PyPI,
  so the pin cannot point at an unpublished version.
- 1a–1d are at minimum one commit (the library PR); 1f is its
  own commit in this repo, per "at minimum one commit per
  phase / one per logical change".

## Success criteria

- The library `setup()` emits JSON unconditionally for daemons;
  the `message` field is clean and pid/thread are structured
  fields; `setup_console()` is unchanged and still
  human-readable.
- The field-name contract is documented in the library and
  linked from its README.
- The `logs` module has real `stestr` tests (it had none) and
  the library `tox` (flake8 + stestr) is green.
- `library-utilities/docs/plans/PLAN-loki.md` is superseded with
  a pointer to this master plan; the library gains **no**
  `requests` dependency and **no** `LokiHandler`.
- `shakenfist-utilities==0.8.5` is on PyPI and the
  `shakenfist` pin is bumped to match; this repo's
  `pre-commit run --all-files` passes and CI installs cleanly.
- No Loki transport code exists yet (that is phase 2).

## Back brief

Before executing, back-brief the operator: confirm the
two-repo + credentialed-release shape, the refined JSON-only
mechanism (flip to unconditional JSON in `v0.8.5` so SF's 103
call sites are untouched, vs phase-0 D4's per-call-site option),
the clean-message change to `SyslogAdapter.process()` (which
alters the exact text of every daemon log line), and the
load-bearing ordering that the pin bump (1f) follows the PyPI
release (1e). Flag that 1e and the PR creation are operator
actions, not sub-agent steps.

## Review checklist for the management session

- [ ] `setup()` truly emits JSON for the default call
      `logs.setup(__name__)` (no `json=` needed); the
      `TextFormatter` branches are gone and the `json` param no
      longer breaks callers.
- [ ] A real daemon log line is a single clean JSON object:
      `message` has no `setproctitle[pid:thread]` prefix and pid
      is a discrete field.
- [ ] `ConsoleAdapter`/`setup_console()` are untouched — CLI
      output is still human-readable (no JSON regression for
      `sf-ctl`).
- [ ] The library gained no `requests` dependency and no
      `LokiHandler`; `PLAN-loki.md` is superseded.
- [ ] Tests exist under `shakenfist_utilities/tests/`, assert
      the above, and `tox` is green in the library repo.
- [ ] The pin bump landed **after** `v0.8.5` hit PyPI; CI here
      installs and all daemons log JSON.
- [ ] Release notes call out the breaking JSON-only change for
      non-SF text consumers.
