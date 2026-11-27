# OIDC authentication phase 9: functional coverage against a real provider

## Prompt

This phase was planned by `/next-phase oidc-authentication` on
2026-11-27, after phase 8 merged as #5028. The master plan is
[PLAN-oidc-authentication.md](PLAN-oidc-authentication.md); read its
phase 9 paragraph and the `Repo` column note above the Execution
table before this file.

The master plan asks for three things, and names them as the reason
this phase is high effort:

> CI topology, a containerised provider, and driving an interactive
> flow headlessly.

## Planning effort

**High**, as the master plan specifies. The planning was mostly
measurement: the question that decides this phase's shape is how much
memory the host that would run the provider actually has, and the
answer (finding 3) rules out the obvious design. Two findings changed
the scope and one of them is a correction to the master plan itself
(finding 1).

## Scope

**In scope:**

* Functional coverage of the human login path against a real
  OpenID Connect provider: discovery, the token exchange at
  `/auth/login`, the credential set it mints, and the key metadata
  phase 6 added.
* Driving `sf-client login` itself, as a subprocess, with the
  person's half of the device authorization grant performed by the
  test.
* The CI topology that stands the provider up: a script in this
  repository and the step in `shakenfist/actions` that calls it.
* A guard that fails, rather than skips, when the lane that is
  supposed to have a provider does not.

**Out of scope, deliberately:**

* **Authentik in CI.** Finding 3 makes two providers unaffordable
  and D1 picks one. Authentik keeps the coverage phase 8 gave it:
  a committed discovery document, a committed claim set, a
  reproducible lab and a walkthrough.
* **The authorization-code flow in CI.** It needs a browser on the
  host running the client, and the primary has none. D5 says what
  covers it instead.
* **Making the device endpoint's usability discoverable.** Phase 8
  deferred this (its Future work, finding 5 and D8) and it is not
  reopened here.
* **Any change to the login or discovery code.** This phase tests
  what phases 3, 4 and 6 built. A defect it finds is an issue and a
  note in Results, not a fix in this branch — see D7.

## What the survey found

Every claim the master plan's phase 9 paragraph makes was checked
against the tree at `1097526e36`. Two are wrong, one is right but
misleading, and the finding that matters most is not in the paragraph
at all.

### Finding 1 — the paragraph understates the gap, and the master plan has been corrected

The paragraph says the exchange "already has functional coverage in
`test_federation.py` under
`shakenfist/deploy/shakenfist_ci/cluster_ci_tests/`", against "a
throwaway in-process JWKS server rather than a real provider". Both
halves are true: the file exists, and
`cluster_ci_tests/test_federation.py:102-126` starts a
`ThreadingHTTPServer` in the test process and hands the issuer its
address.

What the paragraph does not say is that the coverage is of a
*different endpoint*. The sixteen tests in that file exercise
`/auth/federated`, the service-account exchange. Nothing in
`cluster_ci_tests/` mentions `/auth/login`, `/auth/issuers/<>/discover`,
`client_id` or `oidc_scopes`:

```bash
grep -rln 'auth/login\|/discover\|oidc_scopes\|client_id' \
    shakenfist/deploy/shakenfist_ci/cluster_ci_tests/
# no output
```

So phases 3, 4, 6 and 7 — discovery, the login exchange, the
credential set, key metadata and the entire client flow — have **no
functional coverage of any kind**, not merely coverage against a fake
provider. That is a larger phase than the paragraph describes, and it
is why D6 widens the scope beyond a single login test.

Corrected at source: the master plan's phase 9 paragraph now says
this, and the `index.md` row says it too. A later step need not redo
that.

### Finding 2 — the CA this phase needs already exists, and already covers discovery

`tools/ci-jwks-ca.sh` mints a throwaway CA, installs it as
`FEDERATION_JWKS_CA_BUNDLE` on every node, and leaves the signing key
on the primary so the test can issue itself a leaf (issue #3639). It
is called from `shakenfist/actions`'
`.github/workflows/smoke-cluster.yml`, in the step *Trust a throwaway
JWKS certificate authority*, conditioned on
`inputs.stestr_config == 'cluster-ci.conf'`.

The setting is named for JWKS, so whether it also covers the
discovery fetch was worth checking rather than assuming. It does:
`oidc_discovery.fetch()` raises `JWKSTrustAnchorUnusable`
(`shakenfist/external_api/auth.py:1350`), which is the exception
`federation.py:112-125` raises when the bundle cannot be loaded, so
discovery, the JWKS fetch and the login exchange
(`auth.py:3259-3265`) all resolve TLS through the same trust store.

This is load-bearing. A provider standing up in CI needs an HTTPS
certificate the cluster will accept — the API refuses a plaintext
`issuer_url` and a plaintext `jwks_uri`, and should keep doing so —
and finding 2 means the phase needs **no new trust mechanism at
all**: the provider's leaf is signed by the CA that is already
installed. D3 depends on this.

### Finding 3 — the primary has 4 GiB, and that decides the phase

The functional suite does not run on the GitHub runner. It runs **on
the primary**: `smoke-cluster.yml`'s *Run functional tests* step scps
this repository and `client-python` to `$primary`, builds a venv at
`/tmp/functests`, `pip3 install .`s the client, and runs `stestr run
--config cluster-ci.conf --concurrency 5` over ssh. So the primary is
the host that would have to run a provider, and it is also the only
host the cluster is *proven* to reach, which is the pattern the
in-process JWKS server already relies on.

Its memory is not negotiable from this repository.
`shakenfist/actions`' `ansible/ci-topology-slim-primary.yml:52` sets
`sf_node_ram_mb: 8192` for sf-1 to sf-5 and says in the comment above
it that "the primary stays at 4096". Four GiB, shared with sf-api,
the node's own services, the venv and five stestr workers.

Phase 8's lab asks for "Docker with the compose plugin, `openssl`,
`python3` and about 2GB of memory" for four containers, and its
README warns that Authentik's first boot runs database migrations and
"takes a few minutes on a cold host". Keycloak and Authentik together
on a 4 GiB node running the whole suite is not a tight fit, it is not
a fit. This is what D1 and D2 are about.

`ansible/ci-topology-slim-tier.yml:26` is the exception: there the
primary has `ram: 12288` and `cpu: 6`, because it is also a
hypervisor, the network node and an sf-database member. That is the
one lane with room, and D2 puts the provider there.

### Finding 4 — the client can be driven, because CI already installs it

`sf-client` lives in `client-python`, not here, which the master
plan's `Repo` column is emphatic about. But the CI step above
installs it on the primary from source before the suite runs, so a
cluster CI test in *this* repository can invoke `sf-client login` as
a subprocess without any packaging change and without a
`client-python` pull request. Phase 7's flows are in
`shakenfist_client/commandline/auth.py`; `--flow device` and `--flow`
selection are already there, and its module docstring says the device
grant "needs no browser, no loopback listener and no redirect
registration on this host, which is why it is the flow that works
over ssh and inside a container". That is the flow CI can drive.

### Finding 5 — the silent skip is a real auditing problem

`cluster_ci_tests/test_federation.py:329-348`
(`_require_reachable_jwks`) skips five tests when the cluster did not
fetch the JWKS, naming `tools/ci-jwks-ca.sh` as the likely cause.
The script's only caller is in another repository, consumed at
`@main`. While surveying, a `grep` of this repository for the
script's name found no caller and the provisional conclusion was that
those five tests had never run — which was wrong, but took a clone of
`shakenfist/actions` to disprove.

A test that skips when its fixture is missing, and a fixture whose
installer lives in a repository the test cannot see, is a combination
that cannot be audited from either side. D4 does not repeat it.

### What the survey found to be correct

The phase's premise holds: there is no real provider in any lane, the
trade the master plan describes ("the right trade for testing
validation and no use at all for testing a flow") is a fair
description of `test_federation.py`, and `tools/idp-lab/` exists with
both providers configured through their admin APIs and both image
tags pinned, exactly as phase 8's Future work promised phase 9 would
inherit.

## Decisions

1. **Keycloak only.** Finding 3 leaves no room for both, and a
   choice between them is a choice about which risk is worth paying
   for. Keycloak runs as a single container in `start-dev` with an
   embedded database; Authentik needs four containers and a
   multi-minute migration on a cold host. Keycloak is also the
   provider whose device flow worked without special configuration
   in phase 8, where Authentik's needed a flow set at the brand
   (phase 8's finding 5 and D8) — so Authentik in CI would be
   testing our walkthrough's hardest path on our smallest host.
   Authentik keeps the coverage it has: two committed captures, the
   lab, and the walkthrough. This is the decision most likely to be
   argued with, and the argument against it is good: Authentik's
   configuration is the one more likely to rot, so it is the one more
   worth testing. The answer is that CI cannot afford it today and a
   lane that falls over on memory tests nothing at all.

2. **The provider runs on the primary, in the `debian-13-slim-tier`
   lane only.** On the primary because that is where the suite runs
   and the only host the cluster is proven to reach (finding 3); in
   slim-tier because its primary has 12288 MiB against slim-primary's
   4096 (finding 3), and because that lane already carries extra
   processes — it is the Kerbside lane — and finishes in 34 of its 60
   minutes, so it has both the memory and the clock. The other three
   `cluster-ci.conf` lanes are unchanged and pay nothing.

3. **The provider's certificate is signed by the CA
   `ci-jwks-ca.sh` already installed.** Finding 2 proves the cluster
   resolves discovery, JWKS and login through
   `FEDERATION_JWKS_CA_BUNDLE`, and that script already leaves the
   signing key on the primary for exactly this purpose. So the IdP
   step runs *after* the JWKS CA step and reuses its output. No new
   configuration key, no system trust store edit, and no second CA.
   The ordering is a real dependency and 9b states it.

4. **The bring-up script lives here; its caller lives in
   `shakenfist/actions`.** Same split as `ci-jwks-ca.sh`, for the
   same reason: the script is versioned with the tests that depend on
   it, and the workflow is shared by every component. But finding 5
   says do not repeat the silent half of that pattern. So the script
   writes a descriptor file the tests read, and **a guard test fails
   the suite when the lane is slim-tier and the descriptor is
   absent**. Tests skip on the other lanes, where no provider is
   expected; they cannot skip on the lane that is supposed to have
   one. The master plan's `Repo` cell for this phase is corrected to
   `shakenfist, actions`, and this is two pull requests.

5. **The device flow is driven for real, and the authorization-code
   flow is not driven at all.** `sf-client login --flow device` runs
   as a subprocess while the test plays the person: fetch the
   verification URI, post the credentials to Keycloak's login form,
   post the device-grant approval. That is HTML interaction and it is
   brittle, which is the price of testing the flow rather than a
   reimplementation of it; the brittleness is bounded by the pinned
   image tag, and when the tag moves the helper breaks loudly, which
   is what pinning is for. The authorization-code flow needs a
   browser the primary does not have and gets no CI coverage —
   `client-python`'s own unit tests cover it, and this plan says so
   rather than leaving the gap unstated.

6. **Discovery and key metadata are covered too, not just login.**
   Finding 1 says they have no functional coverage at all, they are
   on the path the login test walks anyway, and a real provider is
   the only honest test of discovery — the whole point of phase 3 is
   reading a document we did not write. Adding them costs one request
   each against a provider that is already running.

7. **A defect this phase finds is recorded, not fixed.** Driving a
   real provider end to end for the first time is the most likely
   place in this plan for a phase-3-or-4 bug to surface, and the
   temptation will be to fix it in the same branch. It gets an issue,
   a Results entry and a test marked `expectedFailure` only where the
   defect is in behaviour this phase is adding coverage for. The
   exception is a defect in the *new* test code or the new script,
   which is this phase's own work.

8. **One new test file, and `test_federation.py` is left alone.** It
   covers a different endpoint with a fake provider and is still
   worth having. The new file is
   `cluster_ci_tests/test_oidc_login.py`.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 9a | xhigh | opus | none | Write `tools/ci-idp-keycloak.sh`, which stands a Keycloak up on the primary and configures it for a human login. Model it on `tools/ci-jwks-ca.sh` — read that script first: same `set -eu` reasoning, same "requires the node address variables and the `nodes` list from `ci-environment.sh`, which the caller must have sourced" contract, same positional ssh-user argument, same explicit guards naming the missing variable. Reuse, do not reinvent: `tools/idp-lab/configure-keycloak.sh` already creates a realm, a group, a public client, a group-membership mapper writing the claim into the **identity** token and a person in that group, through Keycloak's admin API, and its header comment records the settings whose defaults do not work; `tools/idp-lab/lab-up.sh` and `make-certificates.sh` show the startup and certificate shape. The differences from the lab: one container not four, no compose file (a bare `docker run` is enough and the lab's compose carries Authentik too), ports bound so the cluster can reach them rather than loopback-only, and **the certificate is issued from the CA `tools/ci-jwks-ca.sh` left on the primary** (`~/.sf-ci-jwks-ca` by default, `SF_CI_JWKS_CA_DIR` to override — see `cluster_ci_tests/test_federation.py:143-160` for how the test already reads it) with a `subjectAltName` covering the address the cluster will use, because phase 8's step 8b found a SAN-less certificate fails on a hostname mismatch that no CA bundle setting can repair. Also enable the client's **direct access grant**, which 9c needs and the lab does not set. Pin the image tag to the one `tools/idp-lab/docker-compose.yml` already pins, and fail loudly rather than pulling `latest`. Install Docker if it is absent, and say in the header comment why that is acceptable on a throwaway CI node and unacceptable anywhere else, in the register `ci-jwks-ca.sh` uses for the same judgement. Write a descriptor the tests read — `~/.sf-ci-idp.json`, mode `u=r,go=`, holding `issuer_url`, `client_id`, `username`, `password`, `group` and `image` — and nothing else: the tests must not have to know Keycloak's admin API. Add a seam test in `shakenfist/tests/` in the manner of `test_ci_cluster_nodes_seams.py`, covering at least that the script exits non-zero with `ci-environment.sh` unsourced, and that the descriptor it writes parses and carries every key 9c reads. Do not touch `.github/`; 9b owns the caller. Do not write any test under `cluster_ci_tests/`; 9c owns those. |
| 9b | medium | sonnet | none | **Lands in `shakenfist/actions`, not this repository**, as its own pull request, and opened only once 9a has merged here — the step tolerates the script being absent from an older component ref, as the JWKS step already does, so the order is safe either way but the reviewer should not have to hold two branches in their head. Add a step to `.github/workflows/smoke-cluster.yml` named *Stand up an identity provider*, immediately **after** *Trust a throwaway JWKS certificate authority* (D3: it consumes that step's CA), conditioned on `inputs.test_kind == 'functional' && inputs.stestr_config == 'cluster-ci.conf' && inputs.topology == 'slim-tier'` — read the existing step and match its shape exactly: the `if [ ! -f "${script}" ]` tolerance, the `. ${GITHUB_WORKSPACE}/ci-environment.sh`, the `${{ inputs.base_image_user }}` argument, a `timeout-minutes` (use 15; Keycloak's first start is slower than a CA mint). Its comment says what it is for, that it is slim-tier only and why — the primary is 4096 MiB in slim-primary and 12288 in slim-tier, cite both topology files — and that the tests fail rather than skip on this lane if it did not run. Change nothing else in that repository. |
| 9c | xhigh | opus | none | Write `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_oidc_login.py`, covering the server half against the real provider. Read `cluster_ci_tests/test_federation.py` first and follow its conventions: the module docstring that says what is covered and what is not and why, `base.BaseNamespacedTestCase`, `requests` used directly wherever the exact status code is the assertion (the client library flattens 401/403/413/429 into typed exceptions), and `SafeHeaderMixin`. The provider descriptor is `~/.sf-ci-idp.json` (D4), read through a helper that: returns it when present; **fails** with a message naming the step in `shakenfist/actions` when it is absent and the suite is on slim-tier; and skips otherwise. Decide "am I on slim-tier" from the cluster rather than an environment variable if there is an existing way to do it — `cluster_ci_tests/test_nodes.py` carves the localhost topology out of the structural minimum and is the precedent to read — and if there is not, use the descriptor's absence plus a shipped variable and say in the docstring which it is. Cover: an issuer created with the provider's real `issuer_url`, `client_id` and `oidc_scopes` round-trips; `/auth/issuers/<>/discover` fills `authorization_endpoint`, `token_endpoint`, `jwks_uri` and `device_authorization_endpoint` from the real document (D6, and compare against the committed `shakenfist/tests/files/idp/keycloak-discovery.json` only for *shape*, never for values — the lab's issuer URL is not CI's); a group-bound mapping rule plus an identity token obtained through the direct access grant exchanges at `/auth/login` for a working credential set; the minted key works against a real endpoint and is scoped as the rule says; the key's metadata from phase 6 reports its expiry, scopes and provenance, and the provenance names the person who logged in (D6); a token whose group claim matches no rule is refused; and the audience trap phase 4's survey found — an issuer configured with the cluster API URL as its audience refuses a login that the correct `client_id` audience accepts — because that is the one misconfiguration the operator guide used to invite and a functional test is where it stops being theoretical. Do not test the client: 9d owns that. Do not change any code under `shakenfist/` outside `deploy/`; if a test cannot be written without a code change, stop and report it as D7 requires. |
| 9d | xhigh | opus | none | Add the client-driven device login to `test_oidc_login.py`, as its own test class. Run `sf-client login --flow device` as a subprocess — it is installed in the suite's venv by CI (finding 4), and `shakenfist_client/commandline/auth.py`'s docstring is the authority on its behaviour: instructions and warnings go to **stderr**, stdout carries only the command's own document, and no secret is ever printed, so the verification URI and user code must be read from the stream the module says carries them and the test must not assert on anything the module promises never to print. While it polls, play the person in a thread: GET the verification URI, post the credentials to Keycloak's login form, and post the device-grant approval. Confine every piece of HTML knowledge to one helper with a docstring saying it is tied to the pinned image tag and that the right response to it breaking is to fix it, not to loosen it, and that the alternative — reimplementing the flow — would test nothing. Assert that the login leaves usable credentials and that the namespace key it cached is the one the cluster minted, and that `sf-client logout` discards them. Then mutation-test the suspicious half with the `mutation-check` skill, each mutant under `timeout` and with `-B`: make the approver thread approve nothing (the test must fail, not hang past the step budget — if it hangs, the deadline is missing and that is a finding); make the login use `--flow browser` where no browser exists; make the descriptor's password wrong; make the approval succeed for a *different* user and confirm the provenance assertion fails. Record the table in Results. A surviving mutant is a missing test. |
| 9e | medium | sonnet | none | Documentation and close-out. `docs/developer_guide/` gains a short account of what the cluster suite now covers for human login and what it deliberately does not — the authorization-code flow has no functional coverage and `client-python`'s unit tests are what cover it (D5), and Authentik's CI coverage is its captures and its lab, not a running provider (D1) — placed with the existing CI documentation rather than in a new page, and `docs/developer_guide/ci.md`'s workflow table updated if the new step belongs in it. Nothing in `docs/` may name a plan, a phase, a decision or a step: a pre-commit hook enforces it. Add `tools/ci-idp-keycloak.sh` to whatever index `tools/` has, if it has one. Then fill this plan's Results and Definition of done, and record in Future work: Authentik in CI once a lane has the memory, the authorization-code flow once a lane has a browser, and anything D7 turned into an issue. |

Steps 9a and 9c both need the descriptor's shape agreed, so 9a lands
first and 9c reads what it wrote rather than what this plan predicts
it will write. 9d depends on 9c's helper. 9b is the other
repository and is sequenced after 9a.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Keycloak plus the suite exhausts the slim-tier primary | D2 picked the 12288 MiB primary deliberately and finding 3 records both numbers. 9a gives the container an explicit memory limit rather than letting it take what it likes, and the lane's own headroom probe (`ci_headroom_launch.sh`, already running in this workflow) is what would show it. If the lane's pass rate or duration moves, the provider comes out of it rather than the lane growing. |
| Docker on an SF node interferes with cluster networking | The slim-tier primary is a hypervisor and the network node, which is the worst case for this, and Docker's own iptables rules are the specific hazard. 9a must check and report, not assume: the first run of the lane is the evidence, and the node-level checks the workflow already runs after the suite are where a broken bridge would show. If it does interfere, the provider moves to a node that is not the network node and 9c's reachability assumption is revisited — that is a plan amendment, not a workaround. |
| The HTML scraping in 9d rots | D5 accepts this explicitly. The tag is pinned, the knowledge is in one helper, and the helper's docstring says the fix is to fix it. A loosened assertion that passes against a changed login page is the failure mode, and the mutation table in 9d is what makes a silently-passing approver visible. |
| The tests skip everywhere and nobody notices | This is finding 5's failure, repeated. D4's guard is the mitigation and it is a done-criterion, not a note: on slim-tier, a missing descriptor fails. The reviewer should check that guard by reading it, because a guard that itself skips is indistinguishable from no guard. |
| The phase finds a real defect and grows to fix it | D7. The reviewer's job is to push back on any code change under `shakenfist/` outside `deploy/` in this branch. |
| 9d's subprocess hangs and burns the step budget | The device grant has an `expires_in` and phase 7's poll loop has a deadline, so the honest failure is a failed assertion. The mutant that approves nothing is there to prove it, and it runs under `timeout`. |

## Definition of done

* [ ] `tools/ci-idp-keycloak.sh` exists, exits non-zero when
      `ci-environment.sh` was not sourced, and names the missing
      variable rather than failing inside a later command.
* [ ] The certificate the provider presents is signed by the CA in
      `~/.sf-ci-jwks-ca` and carries a `subjectAltName` for the
      address the cluster is given. Checked by reading the
      certificate with `openssl x509 -text`, not by the fetch
      happening to work.
* [ ] The descriptor the script writes parses as JSON, is mode
      `u=r,go=`, and carries exactly the keys `test_oidc_login.py`
      reads — asserted by a test, so a key added to one and not the
      other fails.
* [ ] `shakenfist/actions`' `smoke-cluster.yml` runs the new step
      after the JWKS CA step, and only for
      `cluster-ci.conf` on `slim-tier`. Verified by reading the
      rendered condition, and by the other three lanes' runs showing
      the step skipped.
* [ ] On slim-tier, removing the descriptor makes
      `test_oidc_login.py` **fail**; on slim-primary it makes it
      skip. Both demonstrated, not argued.
* [ ] `/auth/issuers/<>/discover` is asserted against the real
      provider's document and fills all four endpoints, including
      `device_authorization_endpoint` read with `json` rather than
      counted with `grep` — phase 8's step 8a proved the counting
      form wrong, because Keycloak publishes that name twice, the
      second time inside `mtls_endpoint_aliases`.
* [ ] A login through `/auth/login` mints a credential set whose
      key works against a real endpoint, and whose metadata reports
      an expiry, the rule's scopes, and provenance naming the person
      who logged in.
* [ ] An issuer whose `audience` is the cluster API URL refuses a
      login that the same issuer with the `client_id` audience
      accepts, asserted in one test, so phase 4's survey finding is
      pinned by CI rather than by a paragraph.
* [ ] `sf-client login --flow device` completes against the real
      provider in CI, driven by the test, and `sf-client logout`
      discards what it cached.
* [ ] No assertion in `test_oidc_login.py` reads a value that
      `shakenfist_client/commandline/auth.py` promises never to
      print. Checked by reading that module's docstring against the
      test.
* [ ] Every behaviour-changing mutation in 9d is killed by a named
      test, recorded in Results. A surviving one is a failed
      definition of done, not a note.
* [ ] No file under `shakenfist/` outside `deploy/` is changed, and
      no file under `docs/plans/` other than this plan, the master
      plan and `index.md`. Checked with
      `git diff --stat develop... -- ':!docs/plans'`.
* [ ] `pre-commit run --all-files` passes, which includes flake8,
      the unit tests, mypy, `check-doc-anchors`,
      `check-plan-phase-references` and `check-plan-status`.
* [ ] The slim-tier lane's duration and pass rate are reported from
      a real run, against the 34-of-60-minutes figure in
      `functional-lanes.yml`'s comment, so D2's memory and clock
      argument is measured rather than predicted.

## Checks run at planning time

Each of these was run against the tree at `1097526e36`, and each is
cheap enough to re-run:

* `grep -rln 'auth/login\|/discover\|oidc_scopes\|client_id'
  shakenfist/deploy/shakenfist_ci/cluster_ci_tests/` — no output,
  which is finding 1.
* `grep -rn 'FEDERATION_JWKS_CA_BUNDLE' --include='*.py' shakenfist/`
  — three call sites in `external_api/auth.py`, one of them the
  discovery fetch, which is finding 2.
* `grep -n 'sf_node_ram_mb\|ram: ' ansible/ci-topology-slim-*.yml` in
  `shakenfist/actions` — 4096 against 12288, which is finding 3.
* `grep -rn 'ci-jwks-ca' .github/ tools/` here — no caller, and the
  caller found in `shakenfist/actions`, which is finding 5.

## Back brief

Before executing any step, the implementing sub-agent back briefs its
understanding of this plan.

Three gates, each cheap now and expensive later:

1. **D1 and D2 together, before 9a starts.** One provider, on the
   slim-tier primary, is the decision the whole phase is built on,
   and it is the one a reviewer is most likely to want argued —
   Authentik's configuration is the more fragile and so the more
   worth testing, and the answer is only that CI cannot afford it.
   If that trade is wrong, nothing else in the plan survives it.
2. **The descriptor's contents, before 9a writes them.** It is the
   whole interface between a shell script and a test suite in
   different parts of the tree, and 9c is written against it. Six
   keys is a guess; the implementer may find a seventh is needed.
3. **D7, before 9c starts.** The first real provider will probably
   find something. Agreeing in advance that it becomes an issue
   rather than a commit on this branch is what keeps this phase from
   turning into a phase 4 bug fix with some tests attached.

This phase runs containers on a CI node in a throwaway cluster, and
touches no live cluster and no production configuration. It does
change a workflow every component in the fleet consumes
(`smoke-cluster.yml`), which is why 9b is scoped to one step and
forbidden from touching anything else there.

## Future work

Filled in by 9e.

## Results

Filled in as the steps complete.
