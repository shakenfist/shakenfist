# Phase 4: Rework CI log-checks and the clingwrap bundle for Loki

## Context

This is phase 4 of
[`PLAN-remove-syslog-forwarding.md`](PLAN-remove-syslog-forwarding.md).
It reworks the two pieces of CI tooling that today depend on the
primary node's aggregated `/var/log/syslog`, so that phase 5 can
delete rsyslog without leaving CI blind:

1. the **log-check scripts** that grep the aggregated syslog for
   forbidden/required patterns; and
2. the **clingwrap debug bundle** that collects `/var/log/syslog`
   per node.

Both are reworked to read from the Loki stood up in phase 3 (and,
for the bundle, from each node's local journald), and both ship as
**new versioned artifacts** so existing `@main` consumers of the
`shakenfist/actions` repo are not broken.

Scope boundaries:
- This phase works in the sibling **`shakenfist/actions`** repo
  (and only there — we deliberately avoid a clingwrap release; see
  the design decisions). The actions PR is operator-created.
- It does **not** switch the main repo's workflows over to the new
  tooling, and it does **not** remove rsyslog — both are phase 5
  ("switches the main-repo workflows over to the new Loki-aware CI
  checks from phase 4").
- It depends on phase 3 (a CI Loki exists, daemons ship to it) and
  on the phase-1 **field-name contract** (logs are JSON with
  `logger_name`/`level`/`message`/… fields, labels
  `{job="shakenfist", daemon, host}`).

## Key references

In `shakenfist/actions`
(`/srv/kasm_profiles/mikal/vscode/src/shakenfist/actions`):
- `tools/ci_log_checks.sh` — the script to supersede. Greps
  `/var/log/syslog` + `syslog.1` (`:8`). Pattern groups: counted
  thresholds "Building new etcd connection" >5000 (`:17,25,36`)
  and "Sent SIGTERM to" >50 (`:18,26,45`); always-forbidden set
  (`:53-72`); v0.8+-only forbidden (`:76-94`); non-`upgrade`-only
  (`:98`); FORBIDDEN_ONCE_STABLE checked only on syslog lines
  ≥1000 (`:123-131`); non-fatal WARNINGs (`:154-156`). Args:
  `branch`, `job_name` (gate the v0.7 vs v0.8+ and upgrade sets).
- `tools/ci_event_checks.sh` — **entirely etcd-centric**
  (`:7,16-26,30-56`); obsolete now etcd is removed.
- `tools/run_remote` (`:6-30`) — runs a command on a host over
  ssh; `copyscript=true` scp's the script to `/tmp` first.
- `ansible/ci-gather-logs.yml` — runs clingwrap **per node**
  (localhost fast path `:2-33`; all hosts `:35-123`), installs it
  from `git+https://github.com/shakenfist/clingwrap` (`:23-25,
  53-54`), `clingwrap gather --target shakenfist-ci-failure
  --output /tmp/<host>.zip` (`:55-57`), `fetch`es the zips
  (`:69-72`), unzips to `/srv/github/bundle/<host>/` (`:110-111`).

In `shakenfist/clingwrap`
(`/srv/kasm_profiles/mikal/vscode/src/shakenfist/clingwrap`):
- `clingwrap/main.py` — `JOB_MAP` (`:171-176`): `file`/`directory`/
  `shell`/`shell_emitter`; `ShellJob` captures stdout with a 30s
  timeout (`:110-136`). `--target` accepts a built-in name, a
  `.cwd` **file path**, or stdin.
- `clingwrap/examples/shakenfist-ci-failure.cwd` — the bundle
  config: the `/var/log/syslog` `file` jobs (`:219-227`),
  `journalctl -u {etcd,libvirtd,sf.target}` `shell` jobs
  (`:103-115`), a `shell_emitter` that records each `sf-*` unit
  file (`:118-128`), `/etc/sf`, `/srv/shakenfist/{instances,
  events,exceptions}`.

In the main repo workflows
(`shakenfist-wt-loki/.github/workflows/`):
- `functional-tests.yml` — `scp -rp tools` to the primary
  (`:310-322`); `tools/run_remote ${primary} "sudo bash
  tools/ci_log_checks.sh develop <job>"` (`:537-538`); inline
  `journalctl -u "sf-*.service"` and syslog greps (`~:545-554`);
  `ansible-playbook .../ci-gather-logs.yml` (`:628-631`); bundle
  zip (`:649`). `scheduled-tests.yml` has the parallels
  (`~:290,330`). **These invocation sites are touched in phase 5,
  not here.**

The phase-1 field contract: `library-utilities/docs/log-record-fields.md`
(JSON field names) and this plan's phase-0 Decisions (label set).

## Design decisions

### New filenames, not git tags

The actions repo has **no tag/version convention** — everything
is consumed at `@main`. So "new versioned" means **new files
alongside the originals** (`ci_log_checks.sh` stays for
unmigrated consumers; a new `ci_log_checks_loki.sh` is added).
Introducing a tagging scheme is a heavier, separate change and is
**not** done here. Phase 5 points the main-repo workflow at the
new files; other consumers keep using the old ones until they
migrate.

### Queries are structured (`| json`), not raw line greps

The old checks grep flat syslog text where a line is
`LEVEL message…`. Logs are now **JSON**, so:
- Use LogQL JSON parsing: `{job="shakenfist"} | json | message =~
  "<pattern>"` (match the parsed `message` field), not a raw
  `|= "<pattern>"` against the JSON line (which would fail on
  JSON-escaped quotes and is brittle).
- **Patterns that span level+message must be restructured.** E.g.
  the old `"ERROR gunicorn"` relied on the flat `ERROR gunicorn …`
  text; in JSON that is `level="ERROR"` + `message="gunicorn …"`,
  never a contiguous substring. Translate to
  `| json | level="ERROR" |~ "gunicorn"` (or similar). Same for
  `"ERROR sf"` in the once-stable set. The implementing agent must
  translate **each** pattern to preserve its *intent* against the
  JSON structure, consulting the field contract. This is the
  high-effort core of 4a.
- Consider expressing the broad "no errors" intent directly via
  the structured `level` field (`| json | level=~"ERROR|CRITICAL"`)
  as a catch-all, keeping specific message-substring checks for
  the non-ERROR conditions (warnings-that-are-bad, specific
  strings). Preserve the existing per-pattern allow/threshold
  semantics.

### Wide query window (CI Loki is fresh per run)

Phase 3 stands up a fresh Loki per CI run, so every line in it is
from this run. The checks can query a wide window (e.g. last 6h)
with a generous `limit` and need no precise start/end — simplest
and robust. Count hits via `jq` over `data.result[].values`.

### The "once stable" boundary → a time anchor

The old FORBIDDEN_ONCE_STABLE set is checked only on syslog lines
≥1000 (a proxy for "after startup churn"). Loki has no line
numbers. Replace with a **time anchor**: find the timestamp of a
reliable steady-state marker log (e.g. the cluster maintainer's
"elected"/stable message or sf-api readiness) and query the
once-stable patterns with `start=<marker ts>`. If no single
reliable marker exists, fall back to a fixed grace (earliest log
ts + ~60s). The agent picks the marker and documents it; this is
the second trickiest adaptation after the JSON translation.

### Drop the etcd checks

etcd is gone (the BYO-MariaDB plan removed it). The new
`ci_log_checks_loki.sh` **omits** the etcd-connection count
threshold and the `"Cannot communicate with etcd…"` pattern, and
**`ci_event_checks.sh` gets no Loki successor** (it was entirely
etcd). Note this in the script and the PR so the dropped coverage
is intentional, not lost.

### Loki URL on the primary

The checks run on the primary via `run_remote`, where Loki lives,
so they default to `http://localhost:3100` (overridable via a
`LOKI_BASE_URL` env/arg).

### clingwrap bundle: actions-hosted `.cwd`, no clingwrap release

To add the Loki dump + per-node journald without a clingwrap
release, ship a **new `.cwd` config in the actions repo** and
point `clingwrap gather --target <path-to-actions/.cwd>` at it
(clingwrap accepts a file-path target). clingwrap itself is
untouched. The new `.cwd`:
- **keeps** the per-node jobs (the `sf-*` unit-file emitter,
  `/etc/sf`, `/srv/shakenfist/{instances,events,exceptions}`,
  libvirtd journal);
- **replaces** the `/var/log/syslog` `file` jobs (`:219-227`,
  which won't exist once rsyslog is gone) with a `journalctl`
  **shell** job capturing the node's local SF logs, e.g.
  `journalctl -u "sf-*.service" --no-pager` (+ keep
  `sf.target`) — so each node's *local* JSON logs are in the
  bundle even when the Loki push is what failed;
- **drops** the `journalctl -u etcd` job (etcd gone).

### Loki dump: primary-only, in the gather playbook

clingwrap runs per node, but Loki is a single central service on
the primary. So the **Loki dump is a one-shot step in a new
gather playbook** (`ci-gather-logs-loki.yml`), gated to the
primary, that `curl`s `query_range` for the run's logs
(`{job="shakenfist"}`, wide window, high limit) into the bundle
directory (so it lands in the aggregated artifact). The per-node
clingwrap invocation (with the new `.cwd`) handles local logs.
Both old `ci-gather-logs.yml` and the new playbook coexist; phase
5 switches the workflow to the new one. `curl`/`jq` are available
on the CI nodes.

## Step items

### 4a — `tools/ci_log_checks_loki.sh` (new, in actions)

A new script mirroring `ci_log_checks.sh`'s structure and its
`branch`/`job_name` gating, but querying Loki instead of grepping
syslog:
- For each forbidden pattern (translated to `| json` LogQL per the
  design), `curl` `query_range`, count hits with `jq`, fail if >
  threshold (0 for forbidden, the existing counts for the counted
  ones); print the first ~20 matches like the original.
- Reproduce the group gating (always / v0.8+ / non-`upgrade` /
  once-stable / warnings), the once-stable **time anchor**, and
  the non-fatal warning behaviour.
- **Omit** the etcd checks. Default `LOKI_BASE_URL` to
  `http://localhost:3100`.
- shellcheck-clean; bash with `set -euo pipefail`, matching the
  repo's script style.
Leave `ci_log_checks.sh` and `ci_event_checks.sh` untouched.

### 4b — Loki-aware clingwrap config + gather playbook (new, in actions)

- Add `ansible/files/shakenfist-ci-failure-loki.cwd` (or similar)
  — a copy of clingwrap's `shakenfist-ci-failure.cwd` with the
  `/var/log/syslog` `file` jobs replaced by a `journalctl -u
  "sf-*.service"`/`sf.target` shell job, and the etcd journal job
  dropped. (Confirm the exact path clingwrap reads a file target
  from.)
- Add `ansible/ci-gather-logs-loki.yml` — a copy of
  `ci-gather-logs.yml` that (a) runs clingwrap per node with
  `--target <the new .cwd>` and (b) adds a **primary-only** task
  that `curl`s Loki `query_range` for `{job="shakenfist"}` over a
  wide window into the bundle dir, so the central Loki view is in
  the artifact. Leave the original playbook and `.cwd` (built-in)
  untouched.
- ansible-lint / yaml-lint clean.

### 4c — Validation

Phase 4's tooling is only fully exercised once phase 5 wires it
into the workflows against the phase-3 Loki. For this phase:
- shellcheck `ci_log_checks_loki.sh`; ansible-lint
  `ci-gather-logs-loki.yml`; yaml-validate the `.cwd`.
- Where feasible, smoke the script against a throwaway local Loki
  seeded with a few SF-shaped JSON lines (assert a known forbidden
  string is caught and a clean log passes). Document the manual
  test in the PR.
- Note explicitly that full CI validation lands in phase 5.

## Step-level guidance

All steps are in the **`shakenfist/actions`** checkout (a separate
repo, on a feature branch — not a worktree of the shakenfist
repo). Sub-agents prepare the branch; the operator opens the PR.

| Step | Effort | Model | Where | Brief for sub-agent |
|------|--------|-------|-------|---------------------|
| 4a | high | opus | actions branch | Write `tools/ci_log_checks_loki.sh`: query Loki (`| json` LogQL) for the forbidden/required pattern set from `ci_log_checks.sh`, translating each pattern to the JSON structure (restructure level+message-spanning ones like "ERROR gunicorn"), preserving the branch/job gating, the counted thresholds, the warning (non-fatal) set, and a time-anchored replacement for the "once stable after line 1000" rule. Omit etcd checks. Default `LOKI_BASE_URL=http://localhost:3100`. shellcheck-clean. Leave the old scripts intact. |
| 4b | medium–high | opus | actions branch | Add a Loki-aware clingwrap config (`.cwd` in actions) that swaps the `/var/log/syslog` file jobs for a `journalctl -u "sf-*.service"`/`sf.target` shell job and drops the etcd journal; add `ci-gather-logs-loki.yml` that runs clingwrap per node with the new `.cwd` and adds a primary-only Loki `query_range` dump into the bundle. Leave originals untouched; ansible-lint clean. |
| 4c | low–medium | sonnet | actions branch | Lint everything (shellcheck/ansible-lint/yaml), and smoke `ci_log_checks_loki.sh` against a throwaway local Loki seeded with sample SF JSON lines; document the manual test. |

## Step ordering and dependencies

- 4a and 4b are independent (different artifacts) and can run in
  parallel.
- 4c follows both.
- One commit per logical unit at minimum: the log-check script;
  the clingwrap config + gather playbook. They can be one PR.
- The actual workflow switch (pointing `functional-tests.yml` /
  `scheduled-tests.yml` at the new script + playbook, and dropping
  the inline syslog greps) is **phase 5**, in the shakenfist repo.

## Success criteria

- A new `tools/ci_log_checks_loki.sh` reproduces the intent of
  every non-etcd check in `ci_log_checks.sh` by querying Loki with
  structured (`| json`) LogQL, including the counted thresholds,
  the branch/job gating, the warning set, and a time-anchored
  "once stable" boundary; shellcheck-clean. The old scripts are
  untouched.
- A new Loki-aware clingwrap `.cwd` collects each node's **local**
  SF journald logs (replacing the syslog file jobs), and a new
  `ci-gather-logs-loki.yml` adds a primary-only **Loki dump** to
  the bundle — so a bundle is useful even when the shipper itself
  failed. Originals untouched.
- etcd checks are intentionally dropped (documented), and
  `ci_event_checks.sh` has no successor.
- Lint passes (shellcheck, ansible-lint, yaml); the new tooling is
  smoke-tested against a sample Loki where feasible.
- Nothing in the main repo's workflows is switched over yet, and
  no rsyslog wiring is removed (both are phase 5).

## Back brief

Before executing, back-brief the operator on: the new-filenames
(not git tags) versioning choice; the JSON-translation of the
patterns (especially the level+message-spanning ones) and that
this is intent-preserving re-expression, not a literal port; the
time-anchor replacement for the "line 1000" stability heuristic
(and which marker is used); dropping the etcd checks; the
actions-hosted `.cwd` (no clingwrap release) and the primary-only
Loki dump; and that full validation is the phase-5 CI run, with
only lint + a local Loki smoke available now. This is also where
to confirm the cross-repo shape (actions branch, operator PR).

## Review checklist for the management session

- [ ] Every non-etcd check from `ci_log_checks.sh` has a
      corresponding Loki query in the new script, with intent
      preserved; level+message-spanning patterns are correctly
      restructured against the JSON fields.
- [ ] Counted thresholds and the branch/job gating match the
      original; the warning set stays non-fatal.
- [ ] The "once stable" time anchor is reliable (the chosen marker
      actually appears) and documented.
- [ ] etcd checks are dropped deliberately and noted; the old
      scripts and the built-in clingwrap `.cwd` are untouched.
- [ ] The new `.cwd` collects local SF journald per node and no
      longer depends on `/var/log/syslog`; the new playbook's Loki
      dump is primary-only and lands in the aggregated bundle.
- [ ] shellcheck/ansible-lint/yaml all pass; the smoke test (if
      run) caught a seeded forbidden line and passed a clean one.
- [ ] No main-repo workflow switched; no rsyslog removed.
