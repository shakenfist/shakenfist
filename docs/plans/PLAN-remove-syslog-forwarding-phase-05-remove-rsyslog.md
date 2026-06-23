# Phase 5: Remove rsyslog forwarding and switch CI to Loki

## Context

This is phase 5 of
[`PLAN-remove-syslog-forwarding.md`](PLAN-remove-syslog-forwarding.md).
It is the cut-over: it deletes the rsyslog deployer surface,
routes gunicorn's own logs so dropping `--log-syslog` is
lossless, adds the remaining `LOKI_TENANT` / `LOKI_AUTH_HEADER`
operator plumbing, switches the main-repo CI workflows from the
syslog-grep checks to the phase-4 Loki tooling, and **realizes
[`PLAN-remove-primary.md`](PLAN-remove-primary.md) phase 1**.

This phase is **atomic by necessity**: once rsyslog is gone there
is no `/var/log/syslog` to grep, so the workflow check switch and
the deployer removal must land together (and depend on phase 3's
CI Loki + `LOKI_BASE_URL` plumbing and on phase 4's new
`shakenfist/actions` artifacts being merged to `@main`).

Dependencies:
- **Phase 2** (the shipper) — done.
- **Phase 3** (CI Loki + `LOKI_BASE_URL` rendered into
  `/etc/sf/config`) — must be merged first.
- **Phase 4** (`ci_log_checks_loki.sh`, the Loki-aware clingwrap
  `.cwd`, `ci-gather-logs-loki.yml`) — must be on `actions@main`
  first, since the shakenfist workflows check out `actions@main`
  and invoke these by name.

Local journald stays — it is free systemd behaviour, not a
shipping pipeline (phase-0 D5).

## Key references (current anchors)

Deployer rsyslog surface (all in `shakenfist/deploy/ansible/`):
- `deploy.yml:175-185` — a standalone `hosts: allsf` play that
  runs the base role with `role_action: "syslog"` and sets
  `syslog_target`. (The base role dispatches `role_action` via
  `roles/base/tasks/main.yml:7` `include_tasks "{{ role_action
  }}.yml"`.)
- `roles/base/tasks/syslog.yml` — deploys the client forwarder
  (`when: syslog_target != node_mesh_ip`) and restarts rsyslog.
- `roles/base/templates/rsyslog-client-01-sf.conf` — the
  `omfwd … port 514` forwarder.
- `roles/primary/templates/rsyslog-server-01-sf.conf` — the
  imudp/imtcp server config.
- `roles/primary/tasks/bootstrap.yml:18-30` — "Write syslog
  file" + "Restart syslog" (deploys the server config).
- `roles/base/tasks/bootstrap.yml:119` (`- rsyslog` in the apt
  package list) and `:135` (`- rsyslog` in the "Enable desired
  services" list).
- `roles/base/defaults/main.yml:12-13` — the `syslog_target`
  default.
- `files/sf-api.service:33` — gunicorn `--log-syslog
  --log-syslog-prefix sf`.
- `roles/base/templates/journald.tmpl` — `ForwardToSyslog` is
  already commented out; no change needed.

gunicorn logging:
- `shakenfist/external_api/gunicorn_config.py` — currently only
  `post_fork` calls `logship.start('sf-api')` (`:67-68`); there is
  **no** `logger_class`. gunicorn names its loggers
  `gunicorn.access`/`gunicorn.error` and sets `propagate=False` on
  them in `Logger.__init__`, with its own stream handlers.

Loki config plumbing (phase 3 did `LOKI_BASE_URL`; phase 5 adds
tenant/auth):
- `shakenfist/deploy/getsf` — MariaDB env reads `:534-614`,
  `/root/sf-deploy` export block `:958-983`; phase 3 added
  `GETSF_LOKI_BASE_URL`/`export LOKI_BASE_URL`.
- `shakenfist/deploy/ansible/deploy.py` —
  `update_if_specified` `:125-135`; phase 3 added
  `loki_base_url`.
- `roles/base/templates/config` — `MARIADB_HOST` is gated on
  `etcd_master` (`:41-47`); phase 3 added an unconditional
  `SHAKENFIST_LOKI_BASE_URL`.

Workflow check sites (repeated across job blocks):
- `.github/workflows/functional-tests.yml` — the pattern recurs
  in the **functional** (`~530-631`), **merge** (`~1102-1189`),
  **ansible-modules** (`~1505-1567`), and **node-lifecycle**
  (`~1743-1818`) jobs. Per block: `ci_log_checks.sh` (e.g. `:538`),
  inline `grep /var/log/syslog` Traceback/forbidden dumps
  (`:545,547` and the combined `:1512,1750`), the
  `systemctl --failed` + `journalctl` + "grep forwarded syslog"
  block (`:554,1126,1519,1757`), `ci_event_checks.sh`
  (`:584,1147,1534,1785`), the `tar czf /var/log/syslog` artifact
  (`:604,1166,1553,1804`), `ci-gather-logs.yml`
  (`:631,1189,1567,1818`), and `chmod … /var/log/syslog`
  (`:369,911,1455,1685`).
- `.github/workflows/scheduled-tests.yml` — one block: `:290`
  (ci_log_checks), `:297` (grep), `:304` (systemd/journal/syslog
  block), `:330` (ci_event_checks), `:349` (tar syslog), `:365`
  (ci-gather-logs), `:180` (chmod syslog).
- The actions `tools/` dir is scp'd to the primary
  (`functional-tests.yml:310-322`), so `ci_log_checks_loki.sh`
  rides along automatically once it is on `actions@main`.

## Design decisions

### Atomic cut-over

rsyslog removal (5a), the gunicorn fix + flag drop (5b), and the
workflow switch (5d) must land **together** in one change (one
commit or one tightly-ordered set), because each half breaks
without the other: removing syslog without switching the checks
leaves the old greps reading a nonexistent file; switching the
checks needs phase 3's Loki and phase 4's scripts to exist.

### gunicorn logging so `--log-syslog` removal is lossless

Dropping `--log-syslog` orphans gunicorn's `gunicorn.access`/
`gunicorn.error` logs (they have `propagate=False` and their own
stream handlers, so `logship.start`'s logger-tree re-pointing does
**not** catch them). Phase 5 must route them, **mode-aware**:
- **Mode A** (`LOKI_BASE_URL` set): gunicorn loggers should reach
  the root logger's Loki handler — set `propagate=True` and clear
  gunicorn's own stream handlers so the lines go to Loki only.
- **Mode B** (unset): leave gunicorn's default stream handlers so
  its logs land on stderr → journald locally (don't orphan them).

Two viable implementations (pick one; recommend the first):
1. A gunicorn **`logger_class`** (the phase-0 D4 decision) defined
   in `gunicorn_config.py` and set via the `logger_class` setting;
   its `__init__` does the mode-aware propagate/clear after
   `super().__init__()`.
2. Extend `logship.start()`'s Mode-A re-pointing (which already
   runs in `post_fork`) to also handle the `gunicorn.access`/
   `gunicorn.error` loggers.
Drop `--log-syslog --log-syslog-prefix sf` from
`sf-api.service:33` **in the same change**.

### Tenant/auth deploy plumbing + the secret consideration

Add `GETSF_LOKI_TENANT` / `GETSF_LOKI_AUTH_HEADER` through getsf →
`deploy.py` (`update_if_specified`, optional) → the config
template (unconditional `SHAKENFIST_LOKI_TENANT` /
`SHAKENFIST_LOKI_AUTH_HEADER`, rendered only when set), mirroring
phase 3's `LOKI_BASE_URL`. **Secret note:** unlike
`MARIADB_PASSWORD` (rendered only on `etcd_master` nodes), the
auth header must render on **every** node (all daemons push), so
it lands in every `/etc/sf/config`. Confirm that file's mode is
already restrictive (it holds other secrets) and document the
all-node exposure. CI sets neither (its Loki is single-tenant,
unauthenticated), so this plumbing is exercised only by review,
not the phase-5 CI run.

### Workflow switch: drop syslog, point at the Loki tooling

Apply the same edit to each job block in both workflows:
- `ci_log_checks.sh …` → `ci_log_checks_loki.sh …` (same
  branch/job args; defaults `LOKI_BASE_URL=http://localhost:3100`
  on the primary).
- **Remove** the inline `grep /var/log/syslog…` Traceback and
  forbidden-pattern dumps. *Optionally* replace the Traceback dump
  with a single inline Loki `query_range` for quick failure triage
  (recommended — keeps the "print the error on failure"
  convenience).
- In the `systemctl --failed` + `journalctl` + "grep forwarded
  syslog" block: **drop** the "grep forwarded syslog from
  /var/log/syslog" part; **keep** `systemctl --failed` and
  `journalctl -u "sf-*.service"`. Additionally, **make the
  system-level failure detection gating** (see the next decision)
  — these conditions are no longer in Loki.
- **Remove** the `ci_event_checks.sh` invocations (etcd is gone;
  no successor).
- **Remove** the `tar czf … /var/log/syslog*` artifact steps.
- `ci-gather-logs.yml` → `ci-gather-logs-loki.yml`.
- Drop `/var/log/syslog` from the `chmod ugo+r /etc/sf/*
  /var/log/syslog` lines (keep `/etc/sf/*`).
Keep `ci_drain_check.sh` (unrelated to syslog). Must stay
actionlint-clean.

### System-level failures: a per-node gating check (not Loki)

A coverage gap surfaced while implementing phase 4: the Loki
shipper carries only SF's **Python application** logs (plus
gunicorn's, after 5b). Several conditions the old central syslog
grep gated on originate from the **kernel / systemd / a process's
stderr**, not SF's logger, so they will **never** appear in Loki:
- `apparmor="DENIED"` (kernel audit),
- `segfault` (kernel),
- `*** Check failure stack trace: ***` (abseil/gRPC C++ fatal on
  stderr),
- `State 'stop-sigterm' timed out. Killing.` /
  `Main process exited, code=exited` /
  `Failed with result 'exit-code'.` (systemd).

The phase-4 `ci_log_checks_loki.sh` therefore does **not** gate on
these (they would be dead checks). Phase 5 must restore that
coverage with a **per-node gating check** — they now live only in
each node's journald. Concretely: a gating `systemctl --failed`
(any failed unit fails the run) plus a `journalctl` grep on every
node for the patterns above, run across the inventory (the
`ci-gather-logs` playbook already fans out per node, so this can
ride a similar per-node step, or a new `tools/ci_node_checks.sh`
invoked per node). Decide the exact home in 5d; the requirement
is that a daemon crash / apparmor denial / abseil fatal on **any**
node fails CI, as it did when syslog was centrally aggregated.

### Interaction with phase 4 (now folded into phase 4)

In **Mode A**, SF's structured logs go *only* to the logship
spool → Loki; they are **not** in journald (the per-module
`SysLogHandler`s were removed). So for "the shipper failed"
debugging, the clingwrap bundle must collect the **logship spool**
(`/srv/shakenfist/spool/logship/*.db`), not just journald —
journald only has the stdout/stderr residual. This is **now part
of the phase-4 plan** (its clingwrap `.cwd` adds a `directory` job
for the spool dir); it becomes load-bearing here, once phase 5
makes Mode A the CI default with no syslog fallback. Verify the
phase-4 work actually collected the spool before relying on it.

## Step items

### 5a — Remove the rsyslog deployer surface

Delete: `roles/base/tasks/syslog.yml`,
`roles/base/templates/rsyslog-client-01-sf.conf`,
`roles/primary/templates/rsyslog-server-01-sf.conf`; the
`deploy.yml:175-185` syslog play; the `roles/primary/tasks/
bootstrap.yml:18-30` syslog tasks; `- rsyslog` from the apt list
(`base/tasks/bootstrap.yml:119`) and the service-enable list
(`:135`); and the `syslog_target` default
(`base/defaults/main.yml:12-13`). Grep the tree afterwards to
confirm no dangling `syslog_target` / `rsyslog` references remain.
ansible-lint clean.

### 5b — gunicorn logging + drop `--log-syslog`

Implement the mode-aware gunicorn logging (a `logger_class` in
`gunicorn_config.py`, or the `logship.start` extension), then
remove `--log-syslog --log-syslog-prefix sf` from
`files/sf-api.service:33`. Verify (unit test or local run) that
in Mode A gunicorn access/error lines reach the root logger (and
thus the Loki handler) and in Mode B they reach stderr/journald.

### 5c — Tenant/auth deploy plumbing

Add `GETSF_LOKI_TENANT` / `GETSF_LOKI_AUTH_HEADER` to getsf
(reads + exports), `deploy.py` (`update_if_specified`, optional),
and the config template (unconditional, rendered when set), per
the secret note. No required checks (empty = unset).

### 5d — Switch the CI workflows + add the per-node system check

Apply the workflow-switch edit (above) to every job block in
`functional-tests.yml` and `scheduled-tests.yml`. This is
mechanical but recurs ~5×; apply consistently and keep
actionlint green. Confirm `ci_log_checks_loki.sh` and
`ci-gather-logs-loki.yml` names exactly match the phase-4
artefacts on `actions@main`.

Also add the **per-node, gating system-level check** (see the
"System-level failures" decision): a `systemctl --failed` + a
`journalctl` grep for apparmor/segfault/abseil/systemd-exit
patterns, run on every node and failing the run on any hit —
restoring the cluster-wide system-failure gating that Loki cannot
provide. This is a `shakenfist/actions` addition (e.g.
`tools/ci_node_checks.sh` fanned out per node, or a step in the
gather playbook); coordinate it with phase 4's actions PR if it
lands as a new actions artefact.

### 5e — Realize remove-primary phase 1

Update [`PLAN-remove-primary.md`](PLAN-remove-primary.md): mark
its phase 1 ("Remove rsyslog aggregation") complete with a pointer
to this plan (mirroring how `PLAN-remove-apache-lb` realised its
phase 3), and update the row in `docs/plans/index.md`.

## Step-level guidance

5a–5c are in the shakenfist repo. 5d edits the large workflow
files (high care, actionlint). Full validation is a CI run; locally
the agent can ansible-lint the deployer, actionlint the workflows,
and unit-test the gunicorn logging.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | medium | sonnet | none | Delete the rsyslog deployer surface (the files, the `deploy.yml` syslog play, the primary syslog tasks, `- rsyslog` from the apt + service lists, the `syslog_target` default). Grep to confirm nothing dangles. ansible-lint clean. |
| 5b | high | opus | none | Add mode-aware gunicorn logging (a `logger_class` in `gunicorn_config.py`, or extend `logship.start`): Mode A → gunicorn loggers propagate to root (Loki) with their own handlers cleared; Mode B → keep stderr→journald. Then drop `--log-syslog --log-syslog-prefix sf` from `sf-api.service:33`. Add a test for both modes. |
| 5c | medium | sonnet | none | Plumb `GETSF_LOKI_TENANT`/`GETSF_LOKI_AUTH_HEADER` through getsf → deploy.py (optional) → config template (unconditional, rendered when set); document the all-node auth-header exposure. |
| 5d | high | sonnet | none | Switch every job block in `functional-tests.yml` and `scheduled-tests.yml`: `ci_log_checks.sh`→`ci_log_checks_loki.sh`; drop the inline `/var/log/syslog` greps (optionally one inline Loki dump), the `ci_event_checks.sh` calls, the syslog `tar` steps, and the `/var/log/syslog` chmod target; drop the "grep forwarded syslog" part; `ci-gather-logs.yml`→`ci-gather-logs-loki.yml`. **Add the per-node gating system check** (`systemctl --failed` + a journald grep for apparmor/segfault/abseil/systemd-exit, failing on any node) since those are no longer in Loki. actionlint clean; names must match the phase-4 actions artefacts. |
| 5e | low | sonnet | none | Mark `PLAN-remove-primary.md` phase 1 complete (pointer here) and update `docs/plans/index.md`. |

## Step ordering and dependencies

- Land only after phase 3 is merged (Loki + `LOKI_BASE_URL`
  plumbing) and phase 4 is on `actions@main` (the new script +
  playbook + `.cwd`, including the spool collection from the
  phase-4 correction above).
- 5a, 5b, 5c are independent; **5b's flag drop and 5d's check
  switch must land together** with 5a (atomic cut-over) so CI is
  never left grepping a nonexistent syslog.
- 5e is documentation, last.

## Success criteria

- The deployer installs/configures **no** rsyslog: the client/
  server configs, `syslog.yml`, the `deploy.yml` syslog play, the
  primary syslog tasks, the `rsyslog` package + service, and
  `syslog_target` are gone; `sf-api.service` no longer passes
  `--log-syslog`.
- gunicorn's access/error logs reach Loki in Mode A and
  stderr/journald in Mode B; dropping `--log-syslog` loses
  nothing.
- `LOKI_TENANT`/`LOKI_AUTH_HEADER` are plumbed through getsf →
  deploy.py → the config template (with the all-node auth-header
  exposure documented).
- Both CI workflows use the phase-4 Loki tooling
  (`ci_log_checks_loki.sh`, `ci-gather-logs-loki.yml`) and have no
  remaining `/var/log/syslog` reads, `ci_event_checks.sh` calls,
  or syslog tar/chmod; actionlint is green and CI is green.
- `PLAN-remove-primary.md` phase 1 is marked complete pointing
  here; `docs/plans/index.md` updated.
- Local journald still captures node-level logs; no rsyslog
  remains anywhere (`grep -ri rsyslog shakenfist/deploy` is
  empty).

## Back brief

Before executing, back-brief the operator on: the **atomic
cut-over** (rsyslog removal + gunicorn flag drop + workflow switch
land together) and its gating on phase 3 merged + phase 4 on
`actions@main`; the gunicorn logging approach chosen (logger_class
vs logship extension) and its mode-aware behaviour; the all-node
rendering of the (secret) auth header; the phase-4 **spool
collection correction** (Mode A logs live only in the spool, not
journald); and that full validation is the CI run, with
ansible-lint/actionlint/unit tests available locally.

## Review checklist for the management session

- [ ] `grep -ri 'rsyslog\|syslog_target' shakenfist/deploy` is
      empty; the syslog play, tasks, templates, package, and
      service enablement are all gone.
- [ ] `--log-syslog` is removed from `sf-api.service`, and a test
      shows gunicorn logs reach Loki (Mode A) / journald (Mode B).
- [ ] tenant/auth plumbing matches the `LOKI_BASE_URL` pattern;
      the auth header's all-node exposure is documented and the
      config file mode is restrictive.
- [ ] Every job block in both workflows is switched (no block left
      grepping `/var/log/syslog` or calling `ci_event_checks.sh`/
      the old `ci-gather-logs.yml`); the new artefact names match
      `actions@main`; actionlint green.
- [ ] The cut-over is atomic — no intermediate state where CI
      greps a nonexistent syslog.
- [ ] `PLAN-remove-primary.md` phase 1 marked complete; index
      updated.
- [ ] The phase-4 clingwrap collects the logship spool (so a
      shipper failure is debuggable); if not, that gap is closed
      before/with this phase.
