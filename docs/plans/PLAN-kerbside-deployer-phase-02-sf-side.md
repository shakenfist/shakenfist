# Phase 2 -- Shaken Fist side: signing key, Kerbside credential, token duration, shadow guard

Part of [PLAN-kerbside-deployer.md](PLAN-kerbside-deployer.md). Phase 1
([PLAN-kerbside-deployer-phase-01-internal-ca.md](PLAN-kerbside-deployer-phase-01-internal-ca.md))
landed in #4430 as `12441652b`; this phase's first commit closes it out.

**Planning effort:** high, as the master plan asks. This phase changes
what the cluster-config play does on every deploy of every cluster,
including the ones that never turn the feature on. Its three
predecessors in the tokens plan (#4003, #4009, kerbside#412) were all
defects in the configuration nobody tested.
**Review effort:** high for step 2, which adds a guard that can stop a
deploy and a credential minted on every run. Medium elsewhere.

## Scope

**In scope:**

* `kerbside_token_duration`, a node role variable rendered as
  `SHAKENFIST_KERBSIDE_TOKEN_DURATION` beside `SHAKENFIST_KERBSIDE_URL`.
  It replaces the `extra_config` advice in `installation.md:214-218`.
* Minting the signing key with `sf-ctl ensure-kerbside-signing-key`
  whenever `kerbside_url` is set (open question 7).
* Minting a dedicated `kerbside` key in the `system` namespace from a
  new secret variable, `kerbside_system_key`, through
  `sf-ctl bootstrap-system-key --key-from-stdin kerbside` (open
  question 5).
* The shadow guard (open question 6), for both `KERBSIDE_URL` and
  `KERBSIDE_TOKEN_DURATION`.
* `sf-ctl show-config` stops redacting numeric values (decision 6),
  which the guard needs.
* A rootless scripted test of all of the above, run in the
  sanity-checks job and as a pre-commit hook, as phase 1 did for
  `internal_ca`.
* The operator documentation and a release note.

**Out of scope, and why:**

* **A real cluster with the feature on.** No CI lane can turn it on:
  `shakenfist/actions` `tools/deploy-collection.sh:65-81` passes a fixed
  `-e` list to `site.yml`, with no way to add `kerbside_url`. That needs
  an operator push to `shakenfist/actions`, which is phase 4's job. This
  phase proves the "on" path with a stub `sf-ctl`, and the "off" path
  on every real lane, which already run with the feature off.
* **The `kerbside` group and anything on a Kerbside host.** Phase 3.
* **Revoking the credential automatically** when `kerbside_system_key`
  is removed (decision 3).
* **Automated signing-key rotation**, as the master plan says.
* **sfcbr itself.** Turning the feature on there means a
  `sfcbr_kerbside_system_key` in 33fl's sops secrets and two lines in
  `group_vars/allsf.yml`, and a Kerbside to point at -- phase 3, or one
  run by hand. That is 33fl's change, made after this phase ships.

## What the survey found

Every claim in the master plan's phase 2 section was checked against
`develop` at `ae1746299`.

### S1 -- the cluster-config play is not in the collection

It is play 6a of `examples/_shared/site.yml:505-606`, which the
collection's README calls the only place that reads inventory groups.
Its `sf-ctl` calls are inline `ansible.builtin.command` tasks delegated
to `cluster_db_host` (`database_tier_hosts[0]`). It is not a role, so it
has no `argument_specs`. The master plan's "argument_specs validate the
new variables" was therefore not possible as written. Decision 1 is
how this phase makes it true.

sfcbr runs this very file: 33fl's `sfcbr.yml:225-240` invokes the
mirror's `examples/_shared/site.yml` as a child `ansible-playbook` run,
and feeds `system_key` from sops through `group_vars/allsf.yml:11-12`.
So what lands here reaches sfcbr with no 33fl change beyond the new
secret.

### S2 -- `bootstrap-system-key` already rotates

Open question 5 left this uncertain. `client/ctl.py:173-192` calls
`Namespace.new('system').add_key(keyname, key)`, and `add_key`'s
docstring (`namespace.py:251-264`) says adding a name already in use
"has always silently overwritten it, which is a rotation: a new hash, a
new nonce". Rotation needs no new behaviour: change the variable and
redeploy.

The same rotation happens on **every** deploy, whether or not the value
changed, because the play mints unconditionally. That is already true
of the `deploy` key (`site.yml:592-605`, `changed_when: true`).
Outstanding tokens for the old nonce stop validating. Kerbside's SF
source uses `shakenfist_client`, whose `_request_url`
(`client-python apiclient.py:454-490`) catches the 401, re-authenticates
once with the unchanged password, and retries. So a re-mint costs
Kerbside one extra authentication, not a failed scrape. Decision 3
relies on this.

Nothing in `sf-ctl` deletes a key. `sf-client namespace delete-key
system kerbside` (client-python `commandline/namespace.py:168`) does.

### S3 -- the signing key's ordering claim holds

`sf-ctl ensure-kerbside-signing-key` exists (`ctl.py:306-319`) and is
idempotent. The cluster-config play (6a) runs before the register plays
(6b, 6c) that restart daemons, so an `sf-api` restarted with
`KERBSIDE_URL` set always finds a key behind it. That is the ordering
`vdi_console_tokens.md:126-167` asks operators to get right by hand.

### S4 -- `show-config` hides the value the guard must compare

`sf-ctl show-config` (`ctl.py:195-205`) redacts any name matching
`SECRET_CONFIG_KEY_RE` (`config.py:48-49`), and `TOKEN` matches
`KERBSIDE_TOKEN_DURATION`. `config.py:36-41` already calls this
over-match harmless only because `--show-secrets` exists.
`redacted_config_items()` (`config.py:1215-1253`), the other dump site,
exempts `bool`, `int` and `float` values for exactly this reason. So the
guard cannot read the row it has to compare without either a code change
or `--show-secrets`, which would put `AUTH_SECRET_SEED` and the signing
key's private PEM into a registered ansible variable. Decision 6.

### S5 -- the shadow has two sources, and the play writes one of them

A `cluster_config` row overrides `/etc/sf/config` at process start
(`config.py:178-195`). Operators reach such rows in two documented ways:

* `vdi_console_tokens.md:73-92` tells them to run
  `sf-ctl set-config KERBSIDE_URL ...` and
  `sf-ctl set-config KERBSIDE_TOKEN_DURATION 300`.
* `installation.md:214-218` tells them to set `KERBSIDE_TOKEN_DURATION`
  through `extra_config`, which the play itself applies on every deploy
  (`site.yml:557-563`). A guard that only reads existing rows would pass
  on the first deploy, after which the play writes the shadow itself.

So the guard has to cover both variables and both sources, and run
before the play's first `set-config`. The master plan named only
`KERBSIDE_URL` and existing rows.

### S6 -- the trigger for the credential cannot be the group

The master plan mints the `kerbside` key "when the `kerbside` group is
non-empty". That group does not exist until phase 3, and an operator
who runs Kerbside some other way -- the case this phase serves on its
own -- has no group at all but needs the credential just as much.
Decision 2.

### S7 -- one issue the master plan cites was closed by mistake

The master plan says #4093 (the mint test never runs in CI) is open. It
was closed on 2026-10-03 by the merge of #4420, the master plan's own
pull request, whose phase table said phase 4 "closes #4093". GitHub
read that as a closing keyword. Nothing makes the test run yet. #4097
and #4367 are open, as stated.

### S8 -- what is still manual for a bring-your-own-Kerbside operator

`vdi_console_tokens.md` asks an operator to: set `KERBSIDE_URL` (the
`kerbside_url` variable since #4024), set `KERBSIDE_TOKEN_DURATION`,
run `sf-ctl ensure-kerbside-signing-key` before rolling the daemons,
and give Kerbside a system-namespace credential. This phase automates
the last three. The rotation runbook stays manual, because automated
rotation is out of scope. The master plan's "retires every manual step"
is corrected to say so.

### Corrections made at source

In this phase's planning commit: the master plan's Situation says #4093
was closed by #4420's merge rather than fixed; open question 5's
"uncertain" is answered from S2; the phase 2 section names the
credential's trigger (S6), the guard's two sources (S5), the
`show-config` change (S4), where the `argument_specs` come from (S1),
and the rotation runbook as the step still left manual (S8).

## Decisions

### D1 -- the Kerbside steps live in the node role, as two entry points

`roles/node/tasks/kerbside_preflight.yml` (the guard) and
`roles/node/tasks/kerbside_credentials.yml` (the signing key and the
credential), each with its own `argument_specs` entry. Play 6a includes
the first as its first task and the second after the `deploy` key, with
`delegate_to` passed in as `cluster_db_host`.

This is the decision a reviewer would most likely question, since the
rest of play 6a is inline. Two reasons outweigh the inconsistency.
`argument_specs` only exist for roles, so this is the only way the
master plan's "argument_specs validate the new variables" can hold for
`kerbside_system_key`. And a role entry point can be driven by a
rootless test against a stub `sf-ctl`, which is how phase 1 tested
`internal_ca`. Inline tasks in `site.yml` could only be tested by a real
deploy, and no lane can turn the feature on yet (Scope).

The `sf-ctl` path becomes a role variable, `sf_ctl_path`, defaulting to
`/srv/shakenfist/venv/bin/sf-ctl`, used only by the two new entry
points. The rest of the role keeps its literal paths; converting them
is not this phase's business.

### D2 -- what triggers what

| Variable set | Guard | Signing key | `kerbside` key | Rendered |
|---|---|---|---|---|
| neither | does nothing | not minted | not minted | nothing |
| `kerbside_url` only | runs | ensured | not minted | URL and duration |
| `kerbside_system_key` only | does nothing | not minted | **refused** | nothing |
| both | runs | ensured | minted | URL and duration |

The credential without a URL is refused, by an assert in
`kerbside_credentials`, because a Kerbside with nothing to point at is a
configuration mistake rather than a deliberate state. Phase 3 makes
`kerbside_system_key` required when the `kerbside` group is non-empty;
this phase keys off the variable alone (S6).

### D3 -- the credential is minted every deploy, and never revoked by one

The `kerbside` key is minted on every deploy where the variable is set,
exactly as the `deploy` key is. Rotation is changing the variable. The
nonce changes every time, which S2 shows Kerbside absorbs with one
re-authentication.

The master plan's mission says a re-run must leave things "undisturbed
when nothing changed". A re-mint with an unchanged value is a
disturbance only to tokens, and the `deploy` key already does it. The
alternative -- an `sf-ctl` mode that compares the stored hash and skips
an unchanged key -- is a new code path in credential handling, to save a
re-authentication. Not worth it here; recorded as Future work.

Removing `kerbside_system_key` does **not** delete the key. A credential
should not vanish because a variable did -- a sops file that failed to
decrypt looks exactly like a removed variable. Revocation is
`sf-client namespace delete-key system kerbside`, documented in step 4.

### D4 -- `kerbside_token_duration` is rendered with the URL, not alone

A node role variable, `type: int`, default `300` (matching
`config.py:1054-1059`), validated as positive. It is rendered as
`SHAKENFIST_KERBSIDE_TOKEN_DURATION` only when `kerbside_url` is set,
inside the same `{% if %}` as the URL: a duration without a URL means
nothing. A change to it changes `/etc/sf/config`, which the existing
restart-on-change already handles.

### D5 -- the guard

`kerbside_preflight` does nothing unless `kerbside_url` is set. When it
is, for each of `KERBSIDE_URL` (against `kerbside_url`) and
`KERBSIDE_TOKEN_DURATION` (against `kerbside_token_duration`):

1. **`extra_config` names it** -- fail. The play would write a shadow
   row this very run (S5). The message names the entry and says to move
   the value into the variable.
2. **A row exists with a different value** -- fail, before anything is
   written. The message names the row, both values, and
   `sf-ctl unset-config <NAME>`.
3. **A row exists with an equal value** -- a warning, and the row is
   left alone. Comparison is on the string form, since `set-config`
   stores `300` as an integer.
4. **No row** -- nothing.

With `kerbside_url` empty the guard does nothing at all, so the
`set-config`-only path the tokens plan documented keeps working. A row
shown as `<redacted>` (an older `sf-ctl` without decision 6) counts as
different, so version skew fails closed with a message rather than
deploying under a shadow.

The guard reads rows with `sf-ctl show-config`, parsed as JSON, never
with `--show-secrets`.

### D6 -- `show-config` stops redacting numbers

`show_config()` adopts the `redacted_config_items()` rule: a value that
is a `bool`, `int` or `float` is shown even when its name matches
`SECRET_CONFIG_KEY_RE`. The argument is the one `config.py:1230-1240`
already makes: a number cannot be a credential, and the three names it
catches (`API_TOKEN_DURATION`, `FEDERATION_MAX_TOKEN_BYTES`,
`KERBSIDE_TOKEN_DURATION`) are tunables an operator reads this output to
confirm. Factor the predicate into one function in `config.py` that both
call, so the two cannot drift. Rejected: `--show-secrets` under
`no_log`, which keeps every cluster secret in a registered variable to
read one integer.

### D7 -- the credential's own checks

`kerbside_system_key` is `type: str`, `no_log: true` in
`argument_specs`, default `""`. When set, `kerbside_credentials` asserts
it differs from `system_key` (the master plan's requirement) and is at
least 16 characters. It reaches `sf-ctl` on stdin only, with
`no_log: true` on the task, mirroring the `deploy` key task
(`site.yml:592-605`), including its `SHAKENFIST_NODE_MESH_IP`
environment.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | medium | sonnet | none | See brief 1. Commit: "sf-ctl: show numeric config values." |
| 2 | high | opus | none | See brief 2. Commit: "Mint Kerbside credentials from the collection." |
| 3 | high | opus | none | See brief 3. Commit: "Test the Kerbside cluster config without root." |
| 4 | medium | sonnet | none | See brief 4. Commit: "Document deploying the Kerbside credentials." |

Step 1 is independent. Step 3 is written after step 2, as in phase 1,
but its mutation list (brief 3) is fixed now so that step 2 is written
against it.

### Brief 1 -- `show-config` shows numbers

* In `shakenfist/config.py`, add a function -- for example
  `config_value_is_secret(key: str, value: Any) -> bool` -- returning
  `SECRET_CONFIG_KEY_RE.search(key) and not isinstance(value, (bool,
  int, float))`. Make `redacted_config_items()` (`config.py:1215`) use
  it, keeping its comment, which explains why.
* In `shakenfist/client/ctl.py` `show_config()` (`:195-205`), use the
  same function. Update its docstring.
* Update the comment at `config.py:36-41`, which says the over-match is
  harmless at `show-config` because of `--show-secrets`: it is now not
  an over-match there at all for numbers.
* Tests in `shakenfist/tests/test_ctl.py`, beside
  `test_show_config_redacts_secrets` (`:392`): an integer
  `KERBSIDE_TOKEN_DURATION` is shown; a *string* under the same name is
  still redacted; `AUTH_SECRET_SEED` is still redacted. If
  `redacted_config_items()` has tests, they must still pass unchanged.
* Mypy covers `config.py`; keep it passing.

### Brief 2 -- the node role entry points and play 6a

Read S1, S5, D1-D5 and D7 first. Files:

* `roles/node/defaults/main.yml` -- add `kerbside_token_duration: 300`,
  `kerbside_system_key: ""` and `sf_ctl_path:
  /srv/shakenfist/venv/bin/sf-ctl`. Rewrite the comment at `:60-64`,
  which says signing-key provisioning remains the operator's job.
* `roles/node/templates/config:81-89` -- render
  `SHAKENFIST_KERBSIDE_TOKEN_DURATION="{{ kerbside_token_duration }}"`
  inside the existing `{% if kerbside_url %}`. Update the comment above
  it: a `cluster_config` row still overrides at process start, and the
  deploy now refuses to run under a differing one.
* `roles/node/meta/argument_specs.yml` -- `kerbside_token_duration`
  (`int`) and `kerbside_system_key` (`str`, `no_log: true`) in the main
  spec beside `kerbside_url` (`:195-208`, whose description also says
  "remain the operator's job": fix it). Add `kerbside_preflight` and
  `kerbside_credentials` entry points, each with the options it reads:
  `kerbside_url`, `kerbside_token_duration`, `extra_config` (a JSON
  string, as play 6a receives it), `sf_ctl_path`, `cluster_db_host`;
  and for credentials also `kerbside_system_key`, `system_key` and
  `node_mesh_ip` (the value play 6a passes as
  `hostvars[cluster_db_host]['node_mesh_ip']`).
* `roles/node/tasks/kerbside_preflight.yml` -- D5. When
  `kerbside_url` is empty, every task is skipped (one `when` on a
  block). Otherwise: an assert that `kerbside_token_duration > 0`; a
  check over `extra_config | from_json` for entries named
  `KERBSIDE_URL` or `KERBSIDE_TOKEN_DURATION`; `{{ sf_ctl_path }}
  show-config`, `changed_when: false`, `delegate_to: "{{
  cluster_db_host }}"`, parsed with `from_json`; then per name, fail on
  a differing value and warn on an equal one, the way `site.yml:246-258` warns about
  `etcd_master` (an `ansible.builtin.debug` whose message leads with a
  capitalised tag; use `WARNING:`). Messages name the row, both values and the exact fix command.
* `roles/node/tasks/kerbside_credentials.yml` -- D2, D3, D7. When
  `kerbside_url` is set: `{{ sf_ctl_path }}
  ensure-kerbside-signing-key`, `changed_when` honest from its output if
  it distinguishes creation from no-op (read `ensure_signing_key()` in
  `shakenfist/util/vdi_tokens.py:140` and the command's output; if it does not, `changed_when: false` and a
  comment saying why). When `kerbside_system_key` is set: assert
  `kerbside_url` is set, the key differs from `system_key` and is at
  least 16 characters, then `{{ sf_ctl_path }} bootstrap-system-key
  --key-from-stdin kerbside` with `stdin`, `no_log: true`,
  `changed_when: true`, and the `SHAKENFIST_NODE_MESH_IP` environment.
  Every command delegates to `cluster_db_host`. The asserts' messages
  must not contain the key.
* `examples/_shared/site.yml` play 6a (`:517-605`) -- include
  `kerbside_preflight` as the first task and `kerbside_credentials`
  after "Bootstrap the system namespace key", with
  `ansible.builtin.include_role`, `name: shakenfist.shakenfist.node`,
  `tasks_from:`, passing `cluster_db_host`, `extra_config` and
  `node_mesh_ip` as vars. Do not change the play's other tasks. Add
  `kerbside_system_key` and `kerbside_token_duration` to the
  operator-variables comment at `site.yml:22-25`.
* Check `examples/cluster/group_vars` and
  `examples/single-node/group_vars`: if they list the Kerbside
  variables, add the new ones, commented out.
* Run `ansible-lint` (pre-commit) and the PR's own smoke cluster (the
  feature-off proof). Do not write the scripted test; step 3 does.

### Brief 3 -- `tools/ci-test-kerbside-cluster-config.sh`

Model it on `tools/ci-test-internal-ca.sh` (phase 1): `set -euo
pipefail`, a temporary directory removed on exit, an empty
`ANSIBLE_CONFIG`, the working tree symlinked in as the collection,
`-i localhost, -c local -e ansible_become=false`, `run_play` /
`run_play_fails` helpers, and a `CURRENT_CASE` named in every failure.

* A stub `sf-ctl`, written by the script into the temp directory and
  passed as `sf_ctl_path`, which appends its argv and its stdin (if any)
  to a log, and answers `show-config` from a JSON file the case writes.
  Make `cluster_db_host` `localhost`.
* A minimal playbook that includes the two entry points in play 6a's
  order with a marker task between them, so a case can tell what ran
  before a failure.
* Cases, each asserting on the stub's log and the ansible output, never
  on task names alone:
  1. **Feature off.** Neither variable set, rows present for both names.
     The stub log contains no `show-config`, no
     `ensure-kerbside-signing-key`, no `bootstrap-system-key`.
  2. **URL only, no rows.** `show-config` then
     `ensure-kerbside-signing-key`, once each; no `bootstrap-system-key`.
  3. **Both set.** `bootstrap-system-key --key-from-stdin kerbside`,
     with the key on stdin and not in argv.
  4. **The key never reaches the output.** Across case 3's run, the
     ansible log does not contain the key value, even at `-v`.
  5. **Equal rows.** Rows equal to both variables (the duration stored
     as an integer): a warning naming each, and the run succeeds.
  6. **A differing `KERBSIDE_URL` row.** The run fails in the guard; the
     message names `sf-ctl unset-config KERBSIDE_URL`; the stub log has
     no `ensure-kerbside-signing-key` or `bootstrap-system-key`.
  7. **A differing `KERBSIDE_TOKEN_DURATION` row.** As case 6.
  8. **`<redacted>` duration row** (an older `sf-ctl`). Fails as case 7.
  9. **`extra_config` names `KERBSIDE_TOKEN_DURATION`,** no row. Fails
     before any write.
  10. **Refused credentials.** The key equal to `system_key`; the key
      set without `kerbside_url`; the key too short. Each fails before
      `bootstrap-system-key` runs, and no message contains the key.
* If `roles/node/templates/config` can be rendered with
  `ansible.builtin.template` from the node role's defaults plus a small
  set of per-host variables, add a case asserting the duration line
  appears only with `kerbside_url` set. If it needs more than about ten
  variables to render, do not add it; say so in the commit message, and
  rely on review plus phase 4.
* Wire it in exactly as phase 1 wired its test: a step in
  `.github/workflows/functional-tests.yml` `sanity_checks` after "Test
  the internal_ca role", and a local pre-commit hook with `files:`
  limited to the two entry points, `roles/node/meta/argument_specs.yml`,
  `roles/node/defaults/main.yml` and the script. List the hook in
  `docs/developer_guide/standards.md` beside `test-internal-ca`.
* **Mutations.** Keep a script outside the tree that applies each of
  these, runs the test, and restores from a copy (not `git checkout`).
  Each must fail, in the case named:
  * the `when: kerbside_url` on the preflight block removed -- case 1;
  * the `extra_config` check removed -- case 9;
  * the differing-row `fail` turned into the warning -- cases 6, 7;
  * string comparison replaced by `==` on the raw values -- case 5;
  * `no_log` removed from the credential task -- case 4;
  * the key passed as an argument instead of on stdin -- case 3;
  * the `system_key` inequality assert removed -- case 10.
  Report the count in the commit message.

### Brief 4 -- documentation

* `docs/operator_guide/installation.md:205-218` -- add
  `kerbside_token_duration` and `kerbside_system_key` to the variable
  table, and replace the `extra_config` advice for
  `KERBSIDE_TOKEN_DURATION` with the variable.
* `docs/operator_guide/vdi_console_tokens.md`:
  * "Enabling the integration" (`:47-94`) -- lead with the variables.
    Keep the `sf-ctl set-config` path, and say that the deploy refuses
    to run when a row differs from a variable that is set (D5), with
    the fix.
  * "Signing key custody" (`:126-167`) -- the deploy now ensures the
    key whenever `kerbside_url` is set, in the play that precedes the
    daemon restarts, which is the ordering the warning there asks for.
    Keep the manual command for clusters not deployed with the
    collection.
  * A short section on the Kerbside credential: what it is for, that it
    is minted every deploy and rotated by changing the variable (D3),
    that removing the variable does not delete it, and the
    `sf-client namespace delete-key system kerbside` command that does.
* The collection README, if it lists node role variables.
* `docs/release_notes/v07-v08.md` -- one entry, written for an operator
  who followed the old docs: the new variables; the signing key and
  credential now minted by the deploy; **and that a deploy now stops if
  a `KERBSIDE_URL` or `KERBSIDE_TOKEN_DURATION` row differs from the
  variable, or if `extra_config` sets either**, with the fix. Also the
  `show-config` change (D6).
* Check every page you touch for a statement this phase made false,
  for example "provisioning the signing key ... remain[s] the
  operator's job". `grep -rn "operator's job" docs/ shakenfist/deploy/`.

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| An operator who followed `vdi_console_tokens.md` has a `KERBSIDE_URL` row, then sets `kerbside_url` to a different value, and their deploy stops. | Intended (open question 6): the alternative is a deploy that silently does not take effect. The message names the fix; the release note says it happens. Brief 3 cases 6 to 9 check each message. |
| The guard fails a cluster that never turned the feature on. | It does nothing unless `kerbside_url` is set. Case 1 asserts no `sf-ctl` call at all; every real CI lane runs with the feature off, so the PR's smoke cluster and the merge lanes exercise it for real. |
| A secret reaches a log. | `no_log` on every task that carries one, stdin rather than argv, asserts whose messages omit the key. Cases 3, 4 and 10 check; two mutations prove they can fail. |
| An older `sf-ctl` still redacts the duration. | Fails closed with a message (D5, case 8). The deploy installs `sf-ctl` from the same release before play 6a runs, so it only happens with a hand-pinned older package. |
| Re-minting the credential each deploy breaks Kerbside's scrape. | S2: the client re-authenticates on a 401. The management session checks `apiclient.py:483-490` is unchanged when reviewing step 2. |
| `include_role` with delegation does not do what the inline tasks did. | Each command task carries its own `delegate_to: "{{ cluster_db_host }}"`; the brief says so. The PR's smoke cluster runs play 6a for real. |

## Definition of done

* `tools/ci-test-kerbside-cluster-config.sh` exits 0 on this branch, the
  sanity-checks job runs it, and all seven mutations in brief 3 fail it
  in the named case.
* `grep -rn "remain the operator's job" shakenfist/deploy/collection/`
  prints nothing.
* `grep -n 'extra_config' docs/operator_guide/installation.md` no
  longer mentions `KERBSIDE_TOKEN_DURATION`.
* `sf-ctl show-config` prints an integer `KERBSIDE_TOKEN_DURATION` and
  still redacts `AUTH_SECRET_SEED` (brief 1's tests).
* The PR's smoke cluster passes, and its log shows the
  `kerbside_preflight` and `kerbside_credentials` tasks skipped.
* No statement about who provisions the signing key or the credential
  differs between `installation.md`, `vdi_console_tokens.md`, the
  collection README, the node role's defaults and `argument_specs`.
* `pre-commit run --all-files` passes, including the new hook.
* The master plan's phase 2 row reads `Complete` with its `Merged` cell
  filled. That is done by phase 3's first commit, per
  `plan-phase-landing`.

## Back brief

Before executing any step, back brief the operator: say how the work
aligns with this plan, and confirm two decisions that are cheap to
change now and awkward after a release.

* **D3** -- the credential is re-minted on every deploy and never
  revoked by one.
* **D6** -- a Shaken Fist code change (`show-config` shows numbers)
  inside a deployer phase.

## Future work

* **Skip re-minting an unchanged key** (D3): an `sf-ctl` mode that
  compares the stored hash, used by both the `deploy` and `kerbside`
  keys, so a redeploy stops rotating nonces.
* **Make #4093 true.** It was closed by #4420's merge, not fixed (S7).
  Phase 4 is still what runs the mint test.
* **Kerbside's own Shaken Fist use-case page is now out of date.**
  `docs/use-cases/shakenfist.md` in the Kerbside repository says
  creating the signing key "is an explicit operator step", and does not
  mention `kerbside_system_key`. It is synced into
  `docs/components/kerbside/` here, so the fix is made in the Kerbside
  repository, alongside the master plan's documentation phase.
* **The rest of play 6a is untested without a cluster.** D1 moves only
  the Kerbside steps into the role. Moving the others would let the
  same harness cover them.
