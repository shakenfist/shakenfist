# Phase 4: Deploy-side BYO — getsf prompts, role deletion, SQL snippet

Parent plan: [PLAN-byo-mariadb.md](PLAN-byo-mariadb.md).

## Prompt

Before responding, read these files so you understand
the current deployer shape and the cuts this phase
makes:

- `shakenfist/deploy/getsf` — 1080-line bash deployer.
  Read lines 1-60 (helpers like `question_start`,
  `record_answer`), the topology-prompt block around
  lines 270-450, and the tail end where it generates
  `topology.json` or hands off to `deploy.py`.
- `shakenfist/deploy/ansible/deploy.py` lines 150-200
  (the `update_if_specified` calls that translate
  `GETSF_*` env vars to ansible variables, including
  the `mariadb_password` generation at line 167).
- `shakenfist/deploy/ansible/deploy.yml` lines 180-220
  (the `mariadb` role invocation that this phase
  deletes, and the `base` role's `config` action
  that ships `/etc/sf/config` to every node).
- `shakenfist/deploy/ansible/roles/mariadb/` —
  five files total
  (`tasks/bootstrap.yml`, `tasks/main.yml`,
  `handlers/main.yml`, `meta/main.yml`,
  `files/90-shakenfist-tuning.cnf`). The whole
  directory goes away in this phase.
- `shakenfist/deploy/ansible/roles/base/templates/config`
  — the `/etc/sf/config` template. Lines 34-46 are
  the MariaDB env-var block; phase 4 rewrites it.
- `shakenfist/deploy/ansible/roles/primary/tasks/cluster_config.yml`
  — nine `SHAKENFIST_MARIADB_HOST=localhost \` lines
  precede every `sf-ctl` invocation. Phase 4
  dissolves the escape hatch by relying on
  `/etc/sf/config` to set `SHAKENFIST_MARIADB_HOST`
  to the operator's host.
- `shakenfist/deploy/ansible/roles/base/tasks/register.yml`
  lines 10-20 (the comment paragraph about
  `etcd_master` + the inline
  `SHAKENFIST_MARIADB_HOST=localhost` for the
  `ensure-mariadb-schema` invocation).

This phase deliberately leaves CI red until phase 5
lands. The master plan calls this out: deleting
`roles/mariadb/` means CI's deploy step no longer
installs a MariaDB, so functional tests can't reach
one. Phase 5 adds a workflow step that installs
MariaDB outside `getsf` and applies the SQL snippet
this phase ships. The two phases should land in
short succession.

One commit per step. Each commit must pass
`pre-commit run --all-files`; functional CI passing
is *not* a per-step requirement, because phase 4
intentionally breaks the install path that CI
exercises today.

## Context

After phases 0-3, the SF *code* tolerates BYO
MariaDB cleanly: the config layer expresses the
two-config orthogonal model, the gRPC tier
construction is correct, the schema and migrations
moved out of daemon startup, and the compatibility
gate refuses misconfigured servers. The remaining
gap is operator-facing: the bundled `getsf` /
`deploy.py` / `roles/mariadb/` pipeline still
installs and tunes a MariaDB server on
`etcd_master[0]`. That is the bundled-install path
this phase deletes.

What `getsf` does today:

- Prompts the operator for topology (nodes,
  hostnames, NICs, IPs, SSH credentials, floating-IP
  block, DNS server, etc.). 30+ prompts.
- Does **not** prompt for any MariaDB credentials.
- Hands the topology to `deploy.py` via `GETSF_*`
  env vars.
- `deploy.py` generates a random 24-character
  `mariadb_password` (line 167) and threads it
  through `deploy.yml` to the bundled `mariadb`
  role and to the `base` role's `/etc/sf/config`
  template.

What `getsf` does after this phase:

- Same topology prompts as today.
- Plus five new prompts for MariaDB connection
  details: host, port, user, password, database
  name. Defaults match the SQL snippet so a
  single-box convenience deploy needs only the host
  and password.
- `deploy.py` accepts the credentials from
  `GETSF_MARIADB_*` env vars; refuses to proceed if
  host or password is empty (no default fallback).
- The bundled `mariadb` role is gone; `deploy.yml`
  no longer references it.
- The `90-shakenfist-tuning.cnf` file moves to
  `examples/mariadb-tuning.cnf` with a short
  comment explaining how to install it.
- The `SHAKENFIST_MARIADB_HOST=localhost`
  ExecStartPre escape hatch in
  `roles/primary/tasks/cluster_config.yml` and
  `roles/base/tasks/register.yml` is dissolved.
  The shell tasks inherit `SHAKENFIST_MARIADB_HOST`
  from `/etc/sf/config` where it now contains the
  operator's host.
- A new `tools/bootstrap-mariadb.sql` snippet is
  shipped, idempotent, ready for operators to
  apply against their MariaDB instance with their
  chosen password.
- `roles/base/templates/config` rewrites: the
  MariaDB block now uses the operator-provided
  variables. The `MARIADB_HOST` block is wrapped
  in `{% if inventory_hostname in
  groups['etcd_master'] %}` so only database-tier
  nodes get direct-access credentials.

The principle: SF is a component slotted into an
operator's infrastructure. Operators bring their
MariaDB; SF prompts them for its address and uses
it.

## Decisions (phase-local)

1. **`tools/bootstrap-mariadb.sql` ships with a
   `__REPLACE_ME__` placeholder for the password.**
   Operators replace it before applying. The
   alternative — accepting `--password=` via
   `mysql` CLI — would require operators to script
   the password into a shell command, which leaks
   it to process listings. Sed-replace into a
   temporary file (or pipe through sed) keeps the
   password out of `ps`.

2. **The snippet uses the SF defaults: database
   `shakenfist`, user `shakenfist`, grants
   `ALL ON shakenfist.*`.** Operators who want
   different names can edit the snippet AND set
   the corresponding `GETSF_MARIADB_*` answers.
   This phase does not add documentation for
   non-default names — that's an unusual case
   not worth surfacing.

3. **`deploy.py` refuses to proceed if
   `mariadb_host` or `mariadb_password` is
   empty.** No default fallback. Operators who
   forgot to provide credentials get a clear error
   pointing at the `tools/bootstrap-mariadb.sql`
   instructions. No silent fallback to
   `localhost` or to a generated password.

4. **The bundled `90-shakenfist-tuning.cnf`
   becomes a documented example at
   `examples/mariadb-tuning.cnf`.** Operators who
   want the tuning copy it into
   `/etc/mysql/mariadb.conf.d/` themselves. A
   comment block at the top of the file explains
   the install path and notes that the tunings
   are reasonable starting values, not
   prescriptions.

5. **`SHAKENFIST_MARIADB_HOST=localhost` escape
   hatch is dissolved** in
   `roles/primary/tasks/cluster_config.yml` and
   `roles/base/tasks/register.yml`. The shell
   commands inherit `SHAKENFIST_MARIADB_HOST`
   from `/etc/sf/config` (which now contains the
   operator's host). The comments above each
   shell task are reworded to reflect the
   change.

6. **`/etc/sf/config` only gets the direct-MariaDB
   block on database-tier nodes.** The template
   conditional is `{% if inventory_hostname in
   groups['etcd_master'] %}`. Non-database nodes
   get only the gateway-host block. The
   `etcd_master` ansible group name is still the
   one in use; PLAN-remove-primary phase 7
   renames it. Phase 4 does not.

7. **CI breakage between phase 4 and phase 5
   landing is accepted.** Per the master plan's
   sequencing note. Phase 4 commits do not need
   to leave functional CI green; the per-commit
   bar is `pre-commit run --all-files` only.
   Phase 5 restores CI by installing MariaDB in
   a workflow step.

8. **`topology.json` compatibility is dropped.**
   Existing operators with `topology.json` files
   that lack the MariaDB block fail at
   `deploy.py` validation. Per master plan
   decision 7 (greenfields only), no shim is
   provided. The cluster is rebuilt against the
   new shape.

9. **The `examples/` directory already exists.**
   The tuning `.cnf` moves there rather than to
   `tools/`, because `tools/` is for things
   operators *run* (the SQL snippet) while
   `examples/` is for things operators *adapt*
   (the tuning file).

## Steps

Five sequential steps. Each step must pass
`pre-commit run --all-files`; functional CI may be
red between phase 4 and phase 5 landing.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | low | sonnet | none | Ship `tools/bootstrap-mariadb.sql` and move the tuning `.cnf`. Create `tools/bootstrap-mariadb.sql` with idempotent `CREATE DATABASE IF NOT EXISTS shakenfist CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;`, `CREATE USER IF NOT EXISTS 'shakenfist'@'%' IDENTIFIED BY '__REPLACE_ME__';`, `GRANT ALL ON shakenfist.* TO 'shakenfist'@'%';`, `FLUSH PRIVILEGES;`. Add a 10-line SQL comment header explaining: the snippet creates the SF database and user, operators replace `__REPLACE_ME__` with their chosen password before applying (e.g. `sed 's/__REPLACE_ME__/mypw/' tools/bootstrap-mariadb.sql | mysql -u root`), the snippet is idempotent and safe to re-run, and that the database/user names match SF's defaults. Move `shakenfist/deploy/ansible/roles/mariadb/files/90-shakenfist-tuning.cnf` to `examples/mariadb-tuning.cnf` (use `git mv`); rewrite its leading comment block to explain it's a recommended-but-optional drop-in for `/etc/mysql/mariadb.conf.d/`, operators copy it themselves, and the tunings are starting values not prescriptions. Verify `tools/` directory exists at repo root (`ls tools/`); if not, create it. `pre-commit run --all-files`. One commit. |
| 2 | high | opus | worktree | Operator-input plumbing in `getsf` and `deploy.py`. (a) In `shakenfist/deploy/getsf`: add five new prompt blocks after the existing topology prompts and before the topology JSON generation. Match the existing `question_start` / `read` / `record_answer` / `question_end` pattern exactly. The five prompts are: `GETSF_MARIADB_HOST` (required, no default — the operator's MariaDB host or IP), `GETSF_MARIADB_PORT` (default `3306`), `GETSF_MARIADB_USER` (default `shakenfist`), `GETSF_MARIADB_PASSWORD` (required, no default), `GETSF_MARIADB_DATABASE` (default `shakenfist`). For the two required-no-default prompts, do not accept an empty answer; loop the prompt until a value is supplied (or echo a clear error message and `exit 1` — match whichever pattern getsf uses elsewhere). Each prompt should include a sentence pointing operators at `tools/bootstrap-mariadb.sql` and the `docs/operator_guide/database.md` BYO section. (b) In `shakenfist/deploy/ansible/deploy.py`: replace the random-password generation at line 167 with explicit reads of `GETSF_MARIADB_HOST`, `GETSF_MARIADB_PORT`, `GETSF_MARIADB_USER`, `GETSF_MARIADB_PASSWORD`, `GETSF_MARIADB_DATABASE`. Use `update_if_specified('mariadb_port', '3306')`, `update_if_specified('mariadb_user', 'shakenfist')`, `update_if_specified('mariadb_database', 'shakenfist')` for the three optional fields. For `mariadb_host` and `mariadb_password`: read with no default, raise `SystemExit('mariadb_host is required; see tools/bootstrap-mariadb.sql and docs/operator_guide/database.md')` if empty. Use the same `update_if_specified` family so the env-var-to-variable plumbing matches the existing pattern. Worktree isolation: this changes the operator-facing prompt UI and the variable-translation contract; getting either wrong silently breaks every deploy. One commit. |
| 3 | high | opus | worktree | Delete the bundled `mariadb` role and rewrite the config template. (a) `git rm -r shakenfist/deploy/ansible/roles/mariadb/` (all five files: `tasks/bootstrap.yml`, `tasks/main.yml`, `handlers/main.yml`, `meta/main.yml`, and the now-moved `90-shakenfist-tuning.cnf` — the file move happened in step 1, but the role-directory removal needs the now-empty `files/` subdir cleaned up too). (b) In `shakenfist/deploy/ansible/deploy.yml`: remove the `### MariaDB` section's `- hosts: etcd_master` block that invokes the role (around lines 188-200). Remove the `mariadb_password: "{{ mariadb_password }}"` var on the subsequent `base` role's `config` action (line ~214) — wait, keep that one. `base/templates/config` still needs the password threaded through. Remove only the role invocation. (c) Rewrite `shakenfist/deploy/ansible/roles/base/templates/config` lines 34-46 (the MariaDB env-var block). The gateway-host block stays on every node (already correct). Wrap the direct-host block (`SHAKENFIST_MARIADB_HOST`, `SHAKENFIST_MARIADB_PORT`, `SHAKENFIST_MARIADB_USER`, `SHAKENFIST_MARIADB_PASSWORD`, `SHAKENFIST_MARIADB_DATABASE`) in `{% if inventory_hostname in groups['etcd_master'] %}` and `{% endif %}`. Change the values from hard-coded references to operator-provided variables: `SHAKENFIST_MARIADB_HOST="{{ mariadb_host }}"`, `SHAKENFIST_MARIADB_PORT={{ mariadb_port }}`, `SHAKENFIST_MARIADB_USER="{{ mariadb_user }}"`, `SHAKENFIST_MARIADB_PASSWORD="{{ mariadb_password }}"`, `SHAKENFIST_MARIADB_DATABASE="{{ mariadb_database }}"`. Worktree isolation: deletes a role, rewrites a template; one typo breaks every node's `/etc/sf/config` on the next deploy. One commit. |
| 4 | medium | sonnet | none | Dissolve the localhost escape hatch. (a) In `shakenfist/deploy/ansible/roles/primary/tasks/cluster_config.yml`: every shell task (around lines 13-90) has an inline `SHAKENFIST_MARIADB_HOST=localhost \` line as the first line of the shell command. Remove every such line. The shell tasks now inherit `SHAKENFIST_MARIADB_HOST` from `/etc/sf/config` via the systemd environment that the shell module respects. Update the leading comment paragraph (around lines 1-11) to remove the localhost-escape-hatch explanation; the new comment should explain that these tasks run `sf-ctl` commands that need direct MariaDB access (which they get from the operator-provided host in /etc/sf/config). (b) In `shakenfist/deploy/ansible/roles/base/tasks/register.yml`: remove the inline `SHAKENFIST_MARIADB_HOST=localhost \` if present (it was around line 17 — verify by grep), and update the comment paragraph above the task (lines 8-17 area) to reflect the new model. Specifically: replace "All sf-ctl commands run on etcd_master[0] with MARIADB_HOST set so they can ..." with prose explaining that `ensure-mariadb-schema` runs on a database-tier node which has direct MariaDB access via `/etc/sf/config`. (c) `pre-commit run --all-files`. One commit. |
| 5 | medium | sonnet | none | Documentation. (a) `docs/operator_guide/database.md` BYO section: rewrite to be the canonical operator workflow. Cover: prerequisites (a MariaDB 10.6+ server reachable from every SF node, that meets the compat requirements section already in this file from phase 1), the SQL snippet at `tools/bootstrap-mariadb.sql` (sed-replace the password, apply it), the optional tuning at `examples/mariadb-tuning.cnf` (operator-installable drop-in), and the `getsf` prompts that ask for connection details. Show a complete single-box example: `apt install mariadb-server` → apply the snippet → optionally drop in the tuning → run `getsf` → answer the new prompts. (b) Update `docs/operator_guide/upgrades.md` to reflect that operators run `sf-ctl ensure-mariadb-schema` against their existing BYO MariaDB after an SF upgrade with schema changes (this content is mostly already there from phase 1; check for any remaining references to bundled MariaDB install). (c) `CLAUDE.md`: update the "Storage: MariaDB and the Database Service" section's tuning-related guidance if any (likely none, since the tuning was previously in the ansible role and not surfaced to developers). (d) `ARCHITECTURE.md`, `README.md`, `AGENTS.md`: search each for `roles/mariadb`, "bundled MariaDB", or similar phrases that imply SF installs MariaDB; update each to reflect the BYO model. The README in particular may have a "deployment" section that promises a turnkey install — update to mention the BYO prerequisite. (e) `pre-commit run --all-files`. One commit. |

## Validation

- `pre-commit run --all-files` passes after each step.
- After step 1: `tools/bootstrap-mariadb.sql` exists,
  `examples/mariadb-tuning.cnf` exists, the
  pre-existing `roles/mariadb/files/90-shakenfist-
  tuning.cnf` no longer exists. A manual
  `cat tools/bootstrap-mariadb.sql | mysql` against
  a local MariaDB instance succeeds (it's
  idempotent; safe to re-run).
- After step 2: `getsf` (run in a test environment)
  prompts for the five MariaDB fields, refuses empty
  answers for host and password, records them to
  `.getsfrc`. `deploy.py` reads them correctly. An
  attempt to run `deploy.py` with no
  `GETSF_MARIADB_HOST` fails fast with a clear error
  message.
- After step 3: `shakenfist/deploy/ansible/roles/`
  no longer contains a `mariadb/` directory.
  `deploy.yml` no longer references the role.
  A grep `grep -rn 'role: mariadb\|roles/mariadb'
  shakenfist/deploy/` returns zero hits. The base
  config template's MariaDB block has the
  `inventory_hostname in groups['etcd_master']`
  conditional.
- After step 4: `grep -rn 'SHAKENFIST_MARIADB_HOST=localhost'
  shakenfist/deploy/` returns zero hits. The
  cluster_config.yml shell tasks no longer have
  inline env overrides.
- After step 5: documentation is consistent. A new
  operator reading just `docs/operator_guide/
  database.md` knows what they need to bring and
  what `getsf` will ask them for.
- CI remains red between phase 4 landing and
  phase 5 landing; this is expected and called
  out.

## Risks

- **`getsf`'s required-no-default prompts**. If the
  loop pattern is mis-implemented, an operator hits
  enter and gets stuck in an infinite loop. The
  brief for step 2 tells the sub-agent to match
  whichever pattern getsf uses elsewhere for
  required answers (there may be precedent — see
  the `read` pattern in the FLOATING_BLOCK or
  topology-node-list prompts). If no precedent
  exists, the sub-agent should pick "echo a
  clear error and exit 1" rather than loop, to
  fail fast and let the operator re-run with the
  right env vars set.
- **The `inventory_hostname in groups['etcd_master']`
  conditional in the config template**. If the
  Jinja syntax is wrong, every node's `/etc/sf/config`
  is malformed and every daemon fails to start
  on the next deploy. The brief for step 3 tells
  the sub-agent to verify with `ansible-playbook
  --syntax-check deploy.yml` or by running the
  Jinja2 template through Python's template engine
  with a fake inventory before declaring done.
- **The localhost escape hatch removal interaction
  with `sf-ctl ensure-mariadb-schema`**.
  `ensure-mariadb-schema` requires `MARIADB_HOST`
  to be set (verified by phase 1's brief). After
  step 4, the shell tasks rely on the env
  inheritance from `/etc/sf/config`. The brief for
  step 4 calls out that the cluster_config.yml
  shell tasks must run on a node where
  `/etc/sf/config` has the direct-host block —
  which is now only `etcd_master` nodes per step
  3's conditional. The cluster_config.yml tasks
  today delegate to `etcd_master[0]`, so the
  ordering works out: tasks delegate to
  `etcd_master[0]`, which has the direct-host
  block, which has `MARIADB_HOST` set to the
  operator's host. If the delegation is missing
  on some task, step 4's sub-agent will see the
  error at the next deploy attempt.
- **CI breakage window**. Between phase 4 landing
  and phase 5 landing, the cluster_ci pipeline's
  install step has no MariaDB to talk to. If the
  window is more than a day or two, the project
  visibly red-bars. Mitigation: land phase 5
  immediately after phase 4. The master plan
  calls this out explicitly.

## Out of scope

- CI workflow MariaDB install — phase 5.
- N=2 sf-database functional CI shape — phase 6.
- ARCHITECTURE/README/AGENTS sweep for the
  broader BYO direction — partial in step 5; the
  more extensive phase-7 docs sweep handles the
  full pass.
- `etcd_master` → `database_node` ansible group
  rename — PLAN-remove-primary phase 7.
- `topology.json` migration shim for operators
  with existing files — out of scope (greenfields
  only).
- The bundled Apache reverse proxy and rsyslog
  install removal — PLAN-remove-primary phases 1
  and 3.

## Back brief

Before executing this phase, please back brief the
operator on:

- The five steps in order with the file boundaries
  for each.
- The deliberate CI-breakage window between phase
  4 and phase 5 landing. Confirm phase 5 is ready
  to spawn immediately after phase 4 commits land.
- The decision to keep the `etcd_master` ansible
  group name in the new template conditional
  (phase 7's rename territory).
- The decision to refuse empty `mariadb_host` /
  `mariadb_password` in `deploy.py` rather than
  fall back to defaults. Operators who forget to
  set them get a clear error.
- The decision to ship the SQL snippet with a
  `__REPLACE_ME__` placeholder and ask operators
  to sed-replace rather than scripting the
  password into a `mysql` command line.
- The plan to land the broader doc sweep
  (ARCHITECTURE/README/AGENTS) in step 5 as a
  starter and finish it in phase 7.
