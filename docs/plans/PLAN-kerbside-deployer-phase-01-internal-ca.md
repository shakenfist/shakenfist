# Phase 1 -- `internal_ca` issues certificates to any host, and renews them

Part of [PLAN-kerbside-deployer.md](PLAN-kerbside-deployer.md). This is
the first phase, so there is no earlier phase to close out.

**Planning effort:** high, as the master plan asks. The survey found that
certificate renewal is not merely broken by a typo but has never been
able to work, and that every hypervisor certificate the collection has
ever issued expires one year after the deploy. The phase that fixes that
touches TLS material on every node of every collection-deployed cluster.
**Review effort:** high for step 2, which decides when every node's SPICE
certificate is reissued. Medium elsewhere.

## Scope

**In scope:**

* Parameters for `internal_ca`'s `host_certificate` and
  `distribute_certificates` entry points: the certificate's file stem, CN,
  DNS and IP SANs, lifetime and renewal window, plus the destination
  directory, file names, owner, group and modes. The defaults reproduce
  today's `/etc/pki/libvirt-spice/` layout exactly.
* Renewal that works. Expiry is checked against the authoritative copy
  on the control node, the condition is the right way round, the old
  certificate is set aside under its own name, and a change to the
  rendered certtool template (CN, SANs, lifetime) also reissues.
* An explicit certificate lifetime, so the cliff is a setting rather
  than an accident of certtool's defaults.
* A rootless, scripted test of the role in `tools/`. It runs from the
  sanity-checks job and from a local pre-commit hook.
* The collection README's account of certificates, and a release note.

**Out of scope, and why:**

* **The `site.yml` play that issues the Kerbside proxy certificate.**
  The master plan put it here. The survey moves it to phase 3, which
  introduces the `kerbside` group and `kerbside_public_fqdn` (decision 1).
  This phase delivers the role that play will call.
* **Rendering `PROXY_HOST_SUBJECT`.** The survey settled its format
  (S5). Reading it back from the installed certificate is phase 3's
  job, when `kerbside.ini` is rendered.
* **The world-readable SPICE private key** (S4). The parameters added
  here make the fix a one-line default change, but choosing the right
  owner and group for QEMU on every supported distribution needs its own
  testing. Recorded under Future work, and filed as #4416.
* **Rotating the key on renewal** (decision 5).
* **Reloading certificates in running instances** (decision 6).

## What the survey found

Filed as #4415, which this phase closes. The master plan's
Situation section reported one defect: the renewal
check stats `server_cert.pem` where `server-cert.pem` is installed. That
is true, but it is the first of four faults. The master plan has been
corrected to match (see "Corrections made at source" below).

### S1 -- renewal has never been able to work

In `roles/internal_ca/tasks/host_certificate.yml`:

1. `:6-9` and `:11-14` stat and expiry-check
   `/etc/pki/libvirt-spice/server_cert.pem`.
   `distribute_certificates.yml:50-55` installs `server-cert.pem`. The
   stat is always false, so nothing after it runs.
2. If the filename were fixed, `:25-29` would crash.
   `host_cert_expires.meta.stdout` is not an attribute of a registered
   `shell` result (the output is `host_cert_expires.stdout`), so the
   `when:` raises on every host with a certificate installed.
3. If that were fixed too, the condition would be inverted.
   `openssl x509 -checkend` prints `Certificate will expire` when the
   certificate *is* within the window. The task moves the certificate
   aside when stdout is **not** that, so it would reissue every healthy
   certificate on every run and keep every expiring one.
4. The move-aside at `:25` renames the certificate to a name derived
   from `host_key_path`. The result is
   `<host>-server-key.pem.<date>.pem` holding a certificate.

Fixing only the typo, as the master plan proposed, would have turned a
silent no-op into a deploy failure on every existing cluster.

### S2 -- every collection-issued certificate expires after 365 days

The host template (`host_certificate.yml:44-56`) sets no
`expiration_days`, so certtool uses its default. Read from sfcbr on
2026-10-03:

| Host | `notBefore` | `notAfter` |
|------|-------------|------------|
| sf-1 | 2026-07-11 07:59 | 2027-07-11 07:59 |
| sf-3 | 2026-07-11 07:59 | 2027-07-11 07:59 |
| sf-6 | 2026-07-16 08:13 | 2027-07-16 08:13 |

The CA (`templates/ca_template.tmpl`) sets 3650 days and expires in 2036.
With S1, every collection-deployed cluster's hypervisor SPICE TLS stops
verifying one year after its first deploy, and redeploying does not help.
The legacy installer this role was ported from carried the same
template, so older clusters may already be past the cliff.

### S3 -- the certificates themselves are fine for rustls

On sf-1 the certificate is X.509 v3 with Basic Constraints, Key Usage,
Extended Key Usage, SKI and AKI (certtool's `tls_www_server`), signed
SHA-256 over a 3072-bit RSA key. rustls rejects v1 certificates; these
are not v1. The certificate carries no subjectAltName, which matters for
the Kerbside proxy (S6) but not for hypervisors, where clients verify
the subject.

### S4 -- the SPICE private key is installed world-readable

`distribute_certificates.yml:40-47` installs `server-key.pem` as
`root:root 0444`, and sfcbr's hypervisors carry exactly that. Out of
scope here (see Scope), but it is why the new parameters include
`cert_key_mode` and owner and group, rather than only paths.

### S5 -- the subject format question is already answered

Master plan open question 4 asked which subject string format the `.vv`
`host-subject` needs, and proposed settling it by test against ryll and
remote-viewer. The answer is already in the tree.
`shakenfist/node.py` `_spice_host_subject_from_cert()` (around
`:120-184`) renders a certificate subject in DER order, as
comma-joined `SHORT=value` pairs with `\` and `,` escaped. That string
is what Shaken Fist puts in the `host-subject` line of its own direct
`.vv` files (`external_api/instance.py` `InstanceVDIConsoleHelperEndpoint`),
which go to the same clients a Kerbside `.vv` goes to. It is also what
Kerbside's Rust proxy compares against
(`kerbside rust/kerbside-proxy/src/backend.rs:439-523`, with escaped
commas preserved). For a collection-issued certificate it renders as
`O=Shaken Fist CA for sfcbr,CN=sf-1`. Phase 3 reuses the renderer's
rules; it does not need a client test.

### S6 -- what the Kerbside certificate needs that hypervisor certificates do not

* **A SAN.** A client that is not given `host-subject` falls back to
  hostname verification, which needs a SAN. certtool templates take
  `dns_name = ...` and `ip_address = ...` lines.
* **A distinct file stem on the control node.** Material is named
  `{{ hostname }}-server-*.pem` (`defaults/main.yml:15-17`). A Kerbside
  proxy co-located on `sf-1` (master plan open question 1) would
  otherwise collide with `sf-1`'s own SPICE certificate.
* **A different destination.** It goes under `/etc/kerbside/pki/`, with
  the key not world-readable.

`site.yml:392-420` passes only `hostname`, so every new parameter must
default to today's value.

### S7 -- nothing executes this role in a test today

Only the template-render test `shakenfist/tests/test_node_config_template.py`
exists, and the stestr environment has no ansible-core (`pyproject.toml`).
Two other things exercise the role:

* ansible-lint, in the sanity-checks job and in pre-commit;
* a real deploy: the PR's `localhost` smoke cluster runs `bootstrap`,
  `host_certificate` and `distribute_certificates` as root with default
  parameters. Renewal is exercised nowhere.

The sanity-checks job (`.github/workflows/functional-tests.yml:165-225`)
runs on `[self-hosted, static]` and installs ansible-lint, and therefore
ansible-core, into `/tmp/venv-test`. Whether the static runner image has
`certtool` (`gnutls-bin`) is **not confirmed**. 33fl's `manage.yml`
installs it on managed servers, but the static runners are built
separately. Step 3 checks.

*Resolved in review.* The image has it: the sanity job ran the new step
on a static runner and printed `internal_ca: all six cases passed`
(run 37158555041, job 111307042604) -- the six-case script as first
pushed; review has since added cases 7 to 9 (brief 3).

### S8 -- running unprivileged is already a supported shape

sfcbr runs the deploy from kasm without root, with `ca_path` pointing at
a user-owned mirror tree (33fl `sfcbr.yml:54-57, 236`). The role already
avoids `owner:` on control-node files for that reason (`bootstrap.yml`
comments). The only task that needs root on the control node is
`bootstrap.yml`'s apt install. A connection variable outranks a task's
`become:` keyword, so `-e ansible_become=false` disables the privileged
slurp at `distribute_certificates.yml:33-38`. That is what makes a
rootless test possible.

### Corrections made at source

Made in the master plan as part of this planning commit:

* The Situation section's first defect bullet now describes S1's four
  faults, S2's lifetime and S4's key mode, rather than only the typo.
* Open question 4 records that S5 answers the format question.
* The phase 1 and phase 3 sections move the Kerbside certificate play
  and the subject read-back to phase 3.

## Decisions

### D1 -- the role, not the play

This phase changes `internal_ca` and adds no `site.yml` play. The master
plan had phase 1 issuing the Kerbside certificate to `kerbside` hosts.
But that play needs the `kerbside` group, `kerbside_public_fqdn` and the
override variables, all of which phase 3 defines. Introducing a group
here with half its meaning would leave `site.yml` describing a deployment
nothing can complete. Phase 3 calls the role with the parameters this
phase adds.

### D2 -- parameters, with today's values as defaults

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `cert_name` | `{{ hostname }}` | File stem on the control node: `{{ ca_path }}/{{ cert_name }}-server-{cert,key,template}.pem`. |
| `cert_cn` | `{{ hostname }}` | Subject CN. |
| `cert_san_dns` | `[]` | `dns_name` lines. |
| `cert_san_ip` | `[]` | `ip_address` lines. |
| `cert_expiration_days` | `365` | Lifetime, now explicit. |
| `cert_renew_days` | `90` | Reissue when the control-node copy expires within this many days. |
| `cert_dest_dir` | `/etc/pki/libvirt-spice` | Install directory on the host. |
| `cert_dest_ca_name` | `ca-cert.pem` | |
| `cert_dest_cert_name` | `server-cert.pem` | |
| `cert_dest_key_name` | `server-key.pem` | |
| `cert_owner` / `cert_group` | `root` / `root` | Owner and group of all three installed files. |
| `cert_mode` | `'0444'` | CA and certificate. |
| `cert_key_mode` | `'0444'` | Unchanged on purpose; see S4 and Future work. |

`host_cert_path`, `host_key_path` and `host_template_path` remain
overridable, now derived from `cert_name` instead of `hostname`. A caller
that sets only `hostname` gets byte-identical paths. The `organization`
line is unchanged.

The `cert_` prefix, rather than the bare names the collection uses
elsewhere, is deliberate. These are role-local knobs, not part of the
cross-role variable contract, and a bare `owner` or `mode` would collide
with names an operator might already have in their group_vars.
`.ansible-lint` skips `var-naming[no-role-prefix]` for the shared
contract, but nothing requires role-local names to be bare.

### D3 -- expiry is checked on the control node's copy

The control node holds the authoritative certificate; the copy on the
host is derived from it on every run. The renewal check therefore reads
`host_cert_path` on the control node with
`openssl x509 -checkend $(( 86400 * cert_renew_days ))` and branches on
the **return code** (0 means not expiring), not on stdout text. That
removes both the filename that went wrong in S1 and the string
comparison that was inverted. The next `distribute_certificates` copies
the new certificate out.

### D4 -- a change to the rendered template also reissues

The template is now written on every run, and a registered change
reissues the certificate. That is the only way a changed
`kerbside_public_fqdn` or SAN list reaches the certificate.

**This is the decision most likely to be argued with**, because adding
an explicit `expiration_days = 365` line changes every existing
template. The first deploy after upgrading therefore reissues every
host's SPICE certificate on every cluster. I think that is acceptable,
and slightly better than avoiding it:

* the key is reused (D5), so nothing that pins a key changes;
* the subject is unchanged, so `spice_server_cert_subject` and Kerbside's
  `host_subject` pinning are unaffected;
* clients pin the CA, which is unchanged;
* running instances keep the certificate QEMU loaded when they started
  (D6) and are not disturbed;
* every cluster gets a fresh 365 days, and lands on a renewal path that
  works, at a point when someone is reading the release note, rather
  than at the cliff.

The alternative was to render the lifetime line only for non-default
values, which keeps existing templates byte-stable. It would leave
clusters at most 365 days from expiry with renewal as their only
defence, and make the template's meaning depend on whether a value
happened to equal its default. The release note says plainly that the
first deploy reissues.

*Step 2 finding.* The template-change trigger is the registered
`changed` flag of the same run. A run interrupted between writing the
template and setting the certificate aside (two adjacent local tasks)
leaves the next run seeing an unchanged template, so that reissue
waits for the expiry window. Comparing the template's mtime with the
certificate's would close the gap, but mtimes are not trustworthy
state here. sfcbr keeps its CA tree in a deploy-mirror checkout, where
mtimes reflect checkout order, so an mtime trigger would reissue or
skip arbitrarily.

*Closed in review.* The gap was an ordering problem, not a missing piece
of state. The template on disk is the record of what the current
certificate was issued from, so it is now compared without being written
-- its sha1 against a hash of the rendered content, which lives in
`vars/main.yml` so both uses share it -- and rewritten only after the old
certificate has been set aside. An interrupted run leaves either the old
certificate with the old template, which the next run compares again, or
no certificate, which the next run issues. Case 7 of the test fails the
run at the set-aside step and proves the rerun reissues. The comparison
is of content alone: a first attempt in review used a check-mode `copy`,
which also compared the file's mode, so a mirrored CA tree whose
checkout resets modes would have reissued every certificate on every
deploy. Case 9 covers both that and the pre-upgrade template.

### D5 -- renewal reuses the key

The key is generated only when absent, as today, and renewal reissues
the certificate against it. Rotating the key on renewal is better
hygiene, but the key is currently world-readable on every hypervisor
(S4). Rotating a key that is already exposed is not the first fix to
make. Future work.

### D6 -- a 90-day renewal window, and a documented limitation

QEMU's SPICE server loads its certificate when the instance starts, and
never reloads it. Step 2 confirmed this against upstream source:

* `qemu_spice_init()` (`ui/spice-core.c`) passes the x509 paths once to
  `spice_server_set_tls()`;
* spice-server's `reds_init_ssl()` reads the files into a single
  `SSL_CTX` that every later connection reuses;
* spice-server exports no reload call;
* QMP's `display-reload` covers VNC only.

A live migration starts a fresh QEMU on the destination, which loads the
current files, so migrating is a way to refresh an instance without
restarting it.

An instance started before a renewal therefore keeps presenting the old
certificate until it is restarted. It fails TLS verification if it is
still running when that certificate expires. A wider window gives such
instances more runway, and 90 days of a 365-day certificate costs
nothing. Renewal also happens only when `site.yml` runs, so the README
says a cluster needs a deploy at least once inside each renewal window.
Reloading certificates in running instances is Future work.

### D7 -- set aside, do not delete

When a certificate is reissued, the old one is moved to
`{{ host_cert_path }}.{{ date }}` on the control node: next to itself,
under its own name (fixing S1.4). Nothing deletes old certificates; they
are small, and they are the only record of what a host presented before.

### D8 -- a scripted, rootless test, run in CI and pre-commit

`tools/ci-test-internal-ca.sh` builds a temporary directory and drives
the role's entry points with `ansible-playbook` against `localhost`,
`connection: local`. It passes:

* `-e ansible_become=false`;
* `--skip-tags packages`. The apt task in `bootstrap.yml` gains that tag,
  and nothing else changes;
* a way past `distribute_certificates.yml`'s "Create /etc/pki/CA" task,
  which needs root and which step 2's rootless smoke run hit. Step 3
  tags it (D8's brief names the tag), and the test skips that tag as
  well;
* `ca_path`, `cert_dest_dir` and `cert_owner`/`cert_group` set to the
  temporary directory and the invoking user.

Its cases and assertions are listed in brief 3. It runs in the
sanity-checks job, after ansible-lint, because that job already has
ansible-core. It also runs as a local pre-commit hook on
`roles/internal_ca/` changes, with the same `language: system` and
`pass_filenames: false` shape as the ansible-lint hook. The script
writes to `$TMPDIR` only and cleans up after itself.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | medium | sonnet | none | See brief 1. Commit: "internal_ca: parameterise certificate paths." |
| 2 | high | opus | none | See brief 2. Commit: "internal_ca: make certificate renewal work." |
| 3 | high | opus | none | See brief 3. Commit: "Test the internal_ca role without root." |
| 4 | medium | sonnet | none | See brief 4. Commit: "Document SPICE certificate lifetime and renewal." |

Step 3's test is written after step 2 rather than first, because a test
of today's renewal would only assert that it crashes. Step 1 is
verified by the PR's own localhost smoke cluster and by a diff of
rendered paths (brief 1).

### Brief 1 -- parameters with today's defaults

Files: `shakenfist/deploy/collection/roles/internal_ca/defaults/main.yml`,
`meta/argument_specs.yml`, `tasks/host_certificate.yml`,
`tasks/distribute_certificates.yml`.

* Add the parameters in D2's table to `defaults/main.yml`, each with a
  one-line comment in the style already there. Re-derive
  `host_cert_path`, `host_key_path` and `host_template_path` from
  `cert_name`.
* Add each parameter to the `&ca_options` anchor in
  `argument_specs.yml` (`type: list`, `elements: str` for the SAN lists;
  `type: int` for the day counts; `type: str` for modes, quoted).
  Correct the `host_certificate` and `distribute_certificates`
  descriptions, which say "SPICE" and `/etc/pki/libvirt-spice`, to say
  "by default".
* In `host_certificate.yml`'s template `content:`, use `cert_cn` for
  `cn`. Emit one `dns_name = ` line per `cert_san_dns` entry and one
  `ip_address = ` line per `cert_san_ip` entry, and nothing when they are
  empty. **Do not add `expiration_days` in this step**; that is step 2's
  behaviour change.
* In `distribute_certificates.yml`, replace every literal
  `/etc/pki/libvirt-spice` and file name with the parameters. Use
  `cert_owner`, `cert_group` and `cert_mode`/`cert_key_mode`. Keep
  creating `/etc/pki/CA`, which is unrelated and still wanted.
* Do not touch the renewal tasks (`host_certificate.yml:5-29`) beyond
  replacing hard-coded paths with the parameters.
* Verify: `ansible-lint --config-file
  shakenfist/deploy/collection/.ansible-lint shakenfist/deploy/collection/`
  is clean. Render `defaults/main.yml` with `hostname=sf-1` and
  `ca_path=/x`, and confirm the three derived paths equal
  `/x/sf-1-server-{cert,key,template}.pem` exactly. A ten-line Python
  snippet with jinja2 is enough; put it in the commit message body,
  not the tree.

### Brief 2 -- renewal that works

File: `roles/internal_ca/tasks/host_certificate.yml` only, plus the
`bootstrap.yml` tag below.

* Replace `:5-29` (the stat, the expiry check, the debug and the
  move-aside) with:
  1. stat `host_cert_path` on the control node (`delegate_to: localhost`,
     as the later tasks do);
  2. if it exists, run `openssl x509 -checkend <seconds> -noout -in
     <host_cert_path>` with `failed_when: false` and
     `changed_when: false`, and treat **return code 1** as "renew";
  3. write the template (moved up from `:44-56`) on **every** run,
     registered, with `expiration_days = {{ cert_expiration_days }}`
     added;
  4. move the certificate aside to
     `{{ host_cert_path }}.{{ ansible_date_time... }}` when it exists
     **and** it expires within the window **or** the template changed.
     `gather_facts` is false in `site.yml`, so compute the date with a
     `date +%Y%m%d%H%M%S` command or a `now()` lookup rather than a
     fact. Make the reason visible in the task name or in a `debug` that
     prints only when renewing.
* Keep "create key only when absent" exactly as it is (D5).
* Re-stat `host_cert_path` after the move-aside, as `:31-36` does now,
  so "create certificate" runs when it is absent.
* Leave the old target-side stat out entirely. Nothing reads the
  installed copy any more (D3).
* Tag `bootstrap.yml`'s "Install CA setup tools" task `packages`.
* Confirm D6's QEMU claim by reading QEMU's `ui/spice-core.c`, the
  functions that set up TLS (`qemu_spice_init` and the
  `x509-dir`/`x509-cert-file` handling). Report whether the certificate
  files are read once at startup, so step 4 can state it. If QEMU does
  reload them, say so; D6's limitation then goes away and the README
  must not claim it.
* Constraints: the role must stay runnable without root on the control
  node (`bootstrap.yml`'s comments explain why; S8). Every control-node
  task delegates to localhost with `delegate_facts: true`, as the
  existing ones do. `changed_when` must be honest: a run in which
  nothing renews and the template is unchanged reports no changes in
  this file.

### Brief 3 -- `tools/ci-test-internal-ca.sh`

New file `tools/ci-test-internal-ca.sh`, executable, `set -euo pipefail`,
shellcheck-clean.

* Fail fast, with a message naming `gnutls-bin`, if `certtool` or
  `ansible-playbook` is missing. Before wiring CI, check whether the
  static runner image installs `gnutls-bin`: look in `mach33labs/33fl`
  for how the static runners are built, or ask the operator. If it does
  not, the fix is a one-line change to that image, which the operator
  makes. Do not `apt install` from the workflow (see the comment above
  `sanity_checks` in `functional-tests.yml`).
* Create a temporary directory and a minimal playbook inside it that
  runs the three entry points via `include_role` with
  `tasks_from:`. Point `ANSIBLE_COLLECTIONS_PATH` at the checkout so
  `shakenfist.shakenfist.internal_ca` resolves to the working tree, not
  an installed collection; `tools/build-collection.py` shows how the
  tree maps onto the collection layout. Run with `-i localhost,`,
  `-c local`, `-e ansible_become=false` and `--skip-tags packages`.
  Set `ca_path`, `cert_dest_dir`, `cert_owner` and `cert_group` to
  the temporary directory and `$(id -un)`/`$(id -gn)`.
* Cases, each asserting with `openssl`, not by grepping ansible output:
  1. **Fresh issue, defaults.** CA and certificate exist. `openssl
     verify -CAfile` passes. Subject CN is the hostname. No SAN. The
     lifetime is within a day of 365 days. The installed files carry the
     requested modes.
  2. **Idempotent.** A second identical run reports `changed=0` in the
     play recap. The certificate's serial and fingerprint are unchanged.
  3. **SANs and a distinct stem.** A second certificate on the same
     "host" with `cert_name=<host>-kerbside`, a different CN and
     `cert_san_dns`/`cert_san_ip` set. It has the SANs, and the first
     certificate is untouched (S6's co-location collision).
  4. **Renewal by expiry.** Replace the control-node certificate with
     one issued from the same template and key, with
     `expiration_days = 1`, using certtool directly. Run again. Assert a
     new serial, `notAfter` about 365 days out, the same public key, the
     old certificate present at `<host_cert_path>.<timestamp>`, and the
     installed copy matching the new one.
  5. **Renewal by template change.** Change `cert_san_dns`. Run again.
     Assert a new serial and the new SAN.
  6. **No renewal outside the window.** A run with nothing changed after
     case 5 issues nothing.

  Review added three more:

  7. **An interrupted reissue is not forgotten.** Change a SAN and fail
     the run at the set-aside step (an `mv` which always fails, first on
     `PATH`). The rerun reissues with the new SAN and the same key.
  8. **Misread values are refused.** An integer or unpadded mode, a CN
     or SAN containing a newline, and a `cert_name` or
     `cert_dest_key_name` containing `../` each fail the run before
     anything changes.
  9. **Upgrade and checkout shapes.** A template whose only change is
     its mode (as a git checkout leaves it) reissues nothing. A template
     as the role wrote it before this phase, without `expiration_days`,
     reissues with the same key and subject.
* Exit non-zero on the first failed assertion, naming the case.
* Wire it in:
  * `.github/workflows/functional-tests.yml` `sanity_checks`: a step
    after "Lint Ansible playbooks" that activates `/tmp/venv-test` and
    runs the script, with `timeout-minutes: 5`.
  * `.pre-commit-config.yaml`: a local hook, `language: system`,
    `files: ^shakenfist/deploy/collection/roles/internal_ca/`,
    `pass_filenames: false`, modelled on the ansible-lint hook beside it.
* Verify locally: the script passes on this branch. Run it against the
  pre-step-2 role (`git stash` or a worktree at step 1's commit) and
  confirm case 4 fails. A test that cannot fail is not one.

### Brief 4 -- documentation

* `shakenfist/deploy/collection/README.md`: a "Certificates" section after
  the existing certtool paragraph (around `:117-120`), covering:
  * what is issued, where, and for how long;
  * that renewal happens on a deploy within `cert_renew_days` of expiry,
    or when the template changes, so a cluster needs a deploy at least
    once per renewal window;
  * that the key is reused;
  * D6's running-instance limitation, as step 2 confirmed it;
  * the parameters, in one table.

  Update the role table's `internal_ca` row (`:21`) to say it can issue
  for any host, not only SPICE.
* A release note in `docs/release_notes/`, in the file for the next
  release. Find it by looking at how recent collection changes were noted.
  It must say three things: renewal never worked, so certificates issued
  more than about nine months ago are near their cliff; the first deploy
  after upgrading reissues every host's certificate with the same key
  and subject (D4); and how to check a host:
  `openssl x509 -in /etc/pki/libvirt-spice/server-cert.pem -noout -enddate`.
* Nothing in `docs/components/` (an import). `ARCHITECTURE.md` and
  `AGENTS.md` are unchanged.

## Risks and mitigations

| Risk | Mitigation, and who checks |
|------|----------------------------|
| The first deploy after upgrade reissues every node's certificate (D4), and something pinned more than the CA and subject. | Brief 3 case 4 asserts the public key is unchanged. The management session checks that nothing in `shakenfist/` or Kerbside pins a certificate fingerprint: `grep -rn fingerprint` over both before merge. The release note says it happens. |
| The static runners lack `certtool`, so the new sanity step fails on every PR. | Brief 3 checks the runner image before wiring CI. The script's first assertion names the package. If the image needs a change, the operator makes it in 33fl before this PR merges. |
| `-e ansible_become=false` does not override the task-level `become: true`, so the rootless test cannot reach the slurp. | Settled by step 2's smoke run: the `-vvv` log shows no become, and the slurp succeeded. No fallback parameter is needed. |
| A rootless control node (sfcbr) cannot write the template over an existing `u=r` file. | `copy` replaces atomically by rename into a directory the user owns, which needs no write permission on the old file. Brief 3 runs entirely rootless, so it proves it. |
| D6 is wrong, and QEMU does reload. | Brief 2 checks the source. Brief 4 states whatever it found, not what this plan assumed. |
| The renewal window passes with no deploy, and certificates still expire. | Documented in the README. A metric or event for certificate expiry belongs to Embrace TLS ("cert expiry within some window emits an event log warning and a prometheus metric"). This phase does not pre-empt it. |

## Definition of done

* `tools/ci-test-internal-ca.sh` exits 0 on this branch and non-zero at
  step 1's commit (case 4), and the sanity-checks job runs it.
* `grep -rn 'server_cert.pem\|\.meta\.stdout'
  shakenfist/deploy/collection/` prints nothing.
* `grep -rn '/etc/pki/libvirt-spice' shakenfist/deploy/collection/roles/internal_ca/tasks/`
  matches only comments. Elsewhere in `roles/`, the hypervisor role's
  libvirt `spice_tls_x509_cert_dir` setting rightly keeps the literal.
* With only `hostname` set, the role's derived control-node paths equal
  today's, as brief 1's check shows.
* The PR's `localhost` smoke cluster passes. That is the real deploy of
  the default path, as root.
* A host certificate issued by this branch reports a `notAfter` 365 days
  after its `notBefore`, set explicitly rather than by certtool's
  default.
* The README states the renewal window, the deploy-cadence requirement
  and the running-instance behaviour, and none of them is stated
  differently in the release note.
* `pre-commit run --all-files` passes, including the new hook and
  ansible-lint.
* The master plan's phase 1 row reads `Complete` with its `Merged` cell
  filled. That is done by phase 2's first commit, per
  `plan-phase-landing`.

## Back brief

Before executing any step, back brief the operator: say how the work
aligns with this plan, and in particular confirm D4 (accepting a
one-time reissue of every node's certificate). D4 is cheap to decide
now and awkward to reverse once a release has reissued certificates
across clusters.

## Future work

* **The SPICE private key is world-readable** (S4). Change
  `cert_key_mode` for the SPICE defaults to `0440` with a group QEMU
  can read: `libvirt-qemu` and `kvm` on Debian and Ubuntu, but this
  needs checking per supported distribution, and against libvirt's
  `spice_tls_x509_cert_dir` expectations. Tracked as #4416.
* **Rotate the key on renewal** (D5), once the key is no longer exposed.
* **A certtool failure mid-reissue leaves no certificate.** The old
  certificate is set aside before certtool runs, so a value certtool
  rejects (a malformed IP SAN) leaves the control node without one until
  the value is corrected. Issuing into temporary files and swapping
  fixes it. Raised in review, tracked as #4435.
* **Running instances outlive their certificate** (D6). Options are a
  QEMU-side reload, if one exists for SPICE, or an operator-visible list
  of instances started before the current certificate.
* **Expiry monitoring** belongs to Embrace TLS. So does the CA itself:
  it is a 3650-day certificate which nothing checks or renews, so from
  about a year before it expires, renewals issue host certificates that
  outlive the CA they chain to.
* **Check older clusters.** Clusters deployed by the legacy installer
  carried the same template and the same broken renewal. Any such
  cluster more than a year old already has expired SPICE certificates.
  The release note's `openssl` one-liner is how an operator finds out.
