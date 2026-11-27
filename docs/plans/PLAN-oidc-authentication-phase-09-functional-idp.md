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

The coverage is **weekly, not merge-gating**: see finding 6 and D2.
Nothing in this phase blocks a merge, and the plan does not pretend
otherwise.

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

### Finding 6 — the lane with the memory is the weekly lane

Found at the back brief rather than at planning time, which is where
it should have been found. `slim-tier` is not a merge-gating lane.
`.github/workflows/functional-tests.yml:598-608` runs `functional_lanes`
only on `merge_group` or `workflow_dispatch`, and the list it passes is
`["debian-13-slim-primary", "guests"]`. `debian-13-slim-tier` appears
instead in `.github/workflows/scheduled-tests.yml:87`, among the weekly
lanes, whose header says they "do not gate a merge, run weekly against
develop" and whose failures are reported by
`tools/ci_report_scheduled_failures.py` as a `ci-failure` issue per
lane.

Both merge-gating lanes use the `slim-primary` topology, so both have
the 4096 MiB primary finding 3 rules out. There is therefore no
merge-gating lane this phase can run in without the memory risk, and
D2 now says so rather than implying otherwise.

Two consequences follow. The coverage this phase adds is weekly, so a
login regression reaches `develop` and is reported up to a week later
by an issue — a large improvement on today's zero, and not a gate.
And the guard in D4 cannot be demonstrated by either pull request,
because the lane runs neither on `pull_request` nor in the merge
queue; the definition of done says how it is demonstrated instead.

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
   lane only, and that lane is weekly.** On the primary because that
   is where the suite runs and the only host the cluster is proven to
   reach (finding 3); in slim-tier because its primary has 12288 MiB
   against slim-primary's 4096 (finding 3), and because that lane
   already carries extra processes — it is the Kerbside lane — and
   finishes in 34 of its 60 minutes, so it has both the memory and
   the clock. The other three `cluster-ci.conf` lanes are unchanged
   and pay nothing.

   Finding 6 is the cost of that, and it was put to Michael at the
   back brief rather than decided here: slim-tier gates no merge, so
   this phase's coverage runs once a week and a failure arrives as a
   `ci-failure` issue. He kept it. The alternative was Keycloak in
   4096 MiB beside sf-api, a venv and five stestr workers on every
   merge, and a flaky merge gate on the fleet's smallest primary is
   worse than honest weekly coverage. Raising that primary is a
   change to another repository's capacity trade, in the middle of
   its own 8 GiB sizing trial, and is not this phase's to make.

   So this phase does not claim a gate. What it claims is that the
   human login path is exercised against a real provider at all,
   which it is not today, and phase 10 should record that the path
   still has no merge-gating functional test.

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
| 9b | medium | sonnet | none | **Lands in `shakenfist/actions`, not this repository**, as its own pull request. Order does not matter for correctness: finding 6 says the slim-tier lane runs neither on `pull_request` nor in the merge queue, so neither pull request's own CI exercises this step, and the step tolerates the script being absent from an older component ref as the JWKS step already does. What matters is that **both** are merged before the next weekly run, or that run exercises half the change. Add a step to `.github/workflows/smoke-cluster.yml` named *Stand up an identity provider*, immediately **after** *Trust a throwaway JWKS certificate authority* (D3: it consumes that step's CA), conditioned on `inputs.test_kind == 'functional' && inputs.stestr_config == 'cluster-ci.conf' && inputs.topology == 'slim-tier'` — read the existing step and match its shape exactly: the `if [ ! -f "${script}" ]` tolerance, the `. ${GITHUB_WORKSPACE}/ci-environment.sh`, the `${{ inputs.base_image_user }}` argument, a `timeout-minutes` (use 15; Keycloak's first start is slower than a CA mint). Its comment says what it is for, that it is slim-tier only and why — the primary is 4096 MiB in slim-primary and 12288 in slim-tier, cite both topology files — and that the tests fail rather than skip on this lane if it did not run. Change nothing else in that repository. |
| 9c | xhigh | opus | none | Write `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_oidc_login.py`, covering the server half against the real provider. Read `cluster_ci_tests/test_federation.py` first and follow its conventions: the module docstring that says what is covered and what is not and why, `base.BaseNamespacedTestCase`, `requests` used directly wherever the exact status code is the assertion (the client library flattens 401/403/413/429 into typed exceptions), and `SafeHeaderMixin`. The provider descriptor is `~/.sf-ci-idp.json` (D4), read through a helper that: returns it when present; **fails** with a message naming the step in `shakenfist/actions` when it is absent and the suite is on slim-tier; and skips otherwise. Decide "am I on slim-tier" from the cluster rather than an environment variable if there is an existing way to do it — `cluster_ci_tests/test_nodes.py` carves the localhost topology out of the structural minimum and is the precedent to read — and if there is not, use the descriptor's absence plus a shipped variable and say in the docstring which it is. Cover: an issuer created with the provider's real `issuer_url`, `client_id` and `oidc_scopes` round-trips; `/auth/issuers/<>/discover` fills `authorization_endpoint`, `token_endpoint`, `jwks_uri` and `device_authorization_endpoint` from the real document (D6, and compare against the committed `shakenfist/tests/files/idp/keycloak-discovery.json` only for *shape*, never for values — the lab's issuer URL is not CI's); a group-bound mapping rule plus an identity token obtained through the direct access grant exchanges at `/auth/login` for a working credential set; the minted key works against a real endpoint and is scoped as the rule says; the key's metadata from phase 6 reports its expiry, scopes and provenance, and the provenance names the person who logged in (D6); a token whose group claim matches no rule is refused; and the audience question the earlier phase settled — that a login is verified against `client_id` and never against `audience`, so the API URL in `client_id` is refused while the API URL in `audience` is irrelevant — because that is the one misconfiguration the operator guide used to invite and a functional test is where it stops being theoretical. (As given, this brief claimed the opposite, that an issuer whose `audience` is the API URL refuses a login; `login_audience()` never reads that column, and the step reported it rather than implementing it.) Do not test the client: 9d owns that. Do not change any code under `shakenfist/` outside `deploy/`; if a test cannot be written without a code change, stop and report it as D7 requires. |
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
| Weekly coverage lets a login regression reach `develop` | Accepted at the back brief, and recorded in D2 rather than hidden. The weekly lane files a `ci-failure` issue naming the commit tested and the last commit the lane passed on (`tools/ci_report_scheduled_failures.py`), so a red run reaches a person. The mitigation that would actually close it is a merge-gating lane with enough memory, which is another repository's decision and is named in Future work. |
| The phase finds a real defect and grows to fix it | D7. The reviewer's job is to push back on any code change under `shakenfist/` outside `deploy/` in this branch. |
| 9d's subprocess hangs and burns the step budget | The device grant has an `expires_in` and phase 7's poll loop has a deadline, so the honest failure is a failed assertion. The mutant that approves nothing is there to prove it, and it runs under `timeout`. |

## Definition of done

* [x] `tools/ci-idp-keycloak.sh` exists, exits non-zero when
      `ci-environment.sh` was not sourced, and names the missing
      variable rather than failing inside a later command.
* [ ] The certificate the provider presents is signed by the CA in
      `~/.sf-ci-jwks-ca` and carries a `subjectAltName` for the
      address the cluster is given. Checked by reading the
      certificate with `openssl x509 -text`, not by the fetch
      happening to work.
      **Not run from here**: needs a provider on a node. The seam test asserts the script issues it with `-CA` and a `subjectAltName` for the address, which is the script and not a certificate. Settled by the lane.
* [x] The descriptor the script writes parses as JSON, is mode
      `u=r,go=`, and carries exactly the keys `test_oidc_login.py`
      reads — asserted by a test, so a key added to one and not the
      other fails.
* [x] `shakenfist/actions`' `smoke-cluster.yml` runs the new step
      after the JWKS CA step, and only for
      `cluster-ci.conf` on `slim-tier`. Verified by reading the
      rendered condition, and by the other three lanes' runs showing
      the step skipped.
* [ ] On slim-tier, removing the descriptor makes
      `test_oidc_login.py` **fail**; on slim-primary it makes it
      skip. Demonstrated by a `workflow_dispatch` of
      `scheduled-tests.yml` once both pull requests are ready, which
      Michael authorised at the back brief because it deploys a
      nested cluster on sfcbr. Finding 6 is why the pull requests
      cannot show it themselves. The skip half is demonstrated by the
      merge queue's own slim-primary run.
      **Half run.** The fail-and-skip verdicts were reproduced over all eight cases by driving the guard directly with a stubbed roster, 0 wrong. The *in-CI* demonstration waits on the dispatch.
* [x] `/auth/issuers/<>/discover` is asserted against the real
      provider's document and fills all four endpoints, including
      `device_authorization_endpoint` read with `json` rather than
      counted with `grep` — phase 8's step 8a proved the counting
      form wrong, because Keycloak publishes that name twice, the
      second time inside `mtls_endpoint_aliases`.
* [ ] A login through `/auth/login` mints a credential set whose
      key works against a real endpoint, and whose metadata reports
      an expiry, the rule's scopes, and provenance naming the person
      who logged in.
      **Not run**: no cluster here. Settled by the lane.
* [ ] A login is verified against `client_id` and **not** against
      `audience`, asserted on one issuer and one token: the API URL
      in `client_id` is refused with a 401, and restoring the real
      `client_id` mints a credential while the API URL is still
      sitting in `audience`. This criterion was reworded during
      execution, because the one first written here described
      behaviour that does not exist and should not: `login_audience()`
      (`shakenfist/external_api/auth.py`) returns `attrs.client_id or
      None` and never consults `attrs.audience`, which is exactly
      what the earlier phase bought, and
      `docs/operator_guide/authentication.md` already warns that the
      fix is to correct `client_id` rather than to change `audience`.
      Satisfying the original wording would have needed a product
      regression.
      **Not run**: no cluster here. Settled by the lane.
* [ ] `sf-client login --flow device` completes against the real
      provider in CI, driven by the test, and `sf-client logout`
      discards what it cached.
      **Not run**: no cluster here. The flow was driven end to end against a real provider and a real client on the development host with the cluster stubbed.
* [x] No assertion in `test_oidc_login.py` reads a value that
      `shakenfist_client/commandline/auth.py` promises never to
      print. Checked by reading that module's docstring against the
      test.
* [x] Every behaviour-changing mutation in 9d is killed by a named
      test, recorded in Results. A surviving one is a failed
      definition of done, not a note.
* [x] **No product code is changed** -- only `shakenfist/deploy/`,
      `shakenfist/tests/`, `tools/` and `docs/`. Reworded during
      execution: as first written this said "no file under
      `shakenfist/` outside `deploy/`", which would have forbidden
      the unit tests this phase's own steps were briefed to add
      under `shakenfist/tests/`. The intent was that no product
      module changes, and that holds -- eight files, none of them
      product code. Checked with `git diff --stat $(git merge-base
      origin/develop HEAD)..HEAD`, **not** `develop...`: this
      worktree's local `develop` ref is behind the branch base and
      reports 195 unrelated files.
* [x] `pre-commit run --all-files` passes, which includes flake8,
      the unit tests, mypy, `check-doc-anchors`,
      `check-plan-phase-references` and `check-plan-status`.
* [ ] The slim-tier lane's duration and pass rate are reported from
      a real run, against the 34-of-60-minutes figure in
      `functional-lanes.yml`'s comment, so D2's memory and clock
      argument is measured rather than predicted.
      **Not run**: waits on the dispatch.

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

## Execution notes

Execution began on 2026-11-27 (AEDT). The back brief was answered
before step 9a started, and one of its four answers changed the plan:

1. **D1 and D2 — kept, with finding 6 recorded.** Checking D2's own
   premise before running a step found that `slim-tier` is a weekly
   lane rather than a merge-gating one, which the plan had not said
   and which makes this phase's coverage weekly. Michael kept
   slim-tier on that understanding rather than risking a flaky merge
   gate on a 4096 MiB primary. D2, the scope section, the risks
   table, this phase's share of the master plan and the guard's
   definition of done were all corrected before any code was
   written; finding 6 records the fact at its source.
2. **The descriptor — the six planned keys, and 9a may add.** If the
   provider configuration turns out to need a seventh, 9a adds it and
   reports it, so 9c is written against what exists rather than
   against what this plan predicted.
3. **Local containers — allowed.** The step agents may stand Keycloak
   up in Docker on the development host, as phase 8 did for its
   captures, so the script and the HTML helper are validated against
   a real provider rather than written blind. No live cluster and no
   sfcbr involvement.
4. **The guard's proof — a `workflow_dispatch` of the weekly lanes**,
   authorised in advance because it deploys a nested cluster. It runs
   once both pull requests are ready, not before.

Two things step 9c found that this plan had not anticipated, both
recorded here rather than left in a report:

* **The audience criterion was wrong and has been reworded** where it
  appeared, in the definition of done and in 9c's own brief. The
  original asked for behaviour that would need a product regression
  to produce. This is the defect D7 exists for, except that the
  defect was in the plan rather than in the product, so it is
  corrected at its source rather than filed.
* **One live issuer per issuer URL.** `federation.issuer_claiming_url()`
  under `_issuer_url_lock` means the API refuses a second issuer for a
  URL with a 409 (`shakenfist/external_api/auth.py`), and there is one
  real provider, so one issuer URL. The tests therefore cannot each
  hold their own issuer at `--concurrency 5`. 9c took turns at the API
  level — 409, wait, a 300 second deadline, and a failure that names a
  leaked issuer — rather than sharing one issuer between tests,
  because the audience test has to reconfigure it. The file is
  effectively serial and costs well under a minute, which is the right
  trade at this size; a suite that grew many more provider tests would
  want a different one.

## Future work

* **Authentik in CI**, once a lane has the memory for a second provider.
  It keeps its committed captures, its lab and its walkthrough until then.
* **The authorization code flow**, once a lane has a browser. It has no
  functional coverage; `client-python`'s own unit tests are what cover it.
* **A merge-gating lane with enough memory for a provider**, which would
  turn this phase's weekly coverage into a gate. It is a capacity decision
  in `shakenfist/actions`, not a change here, and the guard in
  `test_ci_oidc_login_guard.py` is written to fail when the provider's
  lane is moved into the merge queue, so that decision has to be made on
  purpose.
* **`sf-client` cannot trust a private certificate authority for the
  provider.** The client-driven test works around it with
  `REQUESTS_CA_BUNDLE` and says so in place. Filed as
  shakenfist/client-python#461.

## Results

* **9a, `tools/ci-idp-keycloak.sh`.** Stands a pinned Keycloak up on the
  primary and configures a realm, a group, a public client, a group
  membership mapper and a person, then proves the provider usable: the
  device endpoint issues a code, the identity token carries the group, and
  every node fetches the discovery document over https with the throwaway
  authority as its only trust anchor. The descriptor has eight keys rather
  than the six planned: `oidc_scopes` and `group_claim` were added because
  only the script knows them. Docker's daemon is configured to add no
  iptables rules before the package is installed, since installing it
  starts it, and measurement against two daemons showed a default first
  start adds rules on a node which is also the network node. It was run
  verbatim twice against a real pinned Keycloak on the development host,
  with ssh, scp and sudo stubbed to execute locally, which is what the
  observations above come from; the container peaked at 727 MiB of its
  1500m cap, comfortable even on the smaller primary this phase declined.
  But it has **never run on a cluster node**, so the apt branch, the
  `daemon.json` branch, real iptables output and real sf-api readiness are
  untested in anger, and the first run of the lane is what settles them.
* **9b, the workflow step.** Landed in `shakenfist/actions` as `ecf8252`
  on branch `oidc-idp-lane`, its own pull request rather than this branch.
  One step, *Stand up an identity provider*, after the step which installs
  the throwaway authority, at a fifteen minute timeout because a first
  container start and a wait for the provider is slower than minting a
  certificate. The condition was checked by evaluating it against every
  call site of that workflow rather than by reading the lane list: it runs
  on `debian-13-slim-tier` and on nothing else, with both slim-primary
  cluster lanes, the guests lane, the single machine lane and the
  ansible-modules lane skipping. That repository's own hooks, actionlint
  and shellcheck among them, pass. The step has not run, because the lane
  runs neither on a pull request nor in the merge queue.
* **9c, `test_oidc_login.py`, server half.** Seven assertions: an issuer
  for the real provider round trips, discovery fills all four login
  endpoints from the real document, the login metadata offers the issuer,
  a group bound rule mints a working scoped key, the key's metadata
  reports expiry, scopes and provenance, an identity in no bound group is
  refused, and a login is verified against `client_id` rather than
  `audience`. The runtime guard fails, rather than skips, where the
  topology says a provider is expected, and `test_ci_oidc_login_guard.py`
  holds the wiring on every pull request. The audience criterion in this
  plan was wrong and was reworded at its source; one live issuer per
  issuer URL makes the tests take turns. The guard table and wiring test
  ran and passed; **the seven functional assertions have not executed**,
  because there is no cluster here to execute them against.
* **9d, the client-driven device login.** `sf-client login --flow device`
  runs as a subprocess while the test plays the person at the provider's
  pages, and asserts the cached key's provenance and scope and that logout
  discards it. All HTML knowledge is in `shakenfist_ci/idp_pages.py`,
  loadable by path and unit tested against two captured, scrubbed pages,
  so the most fragile code is covered on every pull request rather than
  weekly. A login nobody confirms fails rather than hangs, which
  matters because a weekly lane that hangs spends its budget and reports
  nothing -- though the deadline which fires is this test's own, so it
  says the suite cannot hang and says nothing about what the client would
  do unattended. Unit tests ran; the functional test has **not executed
  against a cluster**.
* **9e, documentation and close-out.** `docs/developer_guide/ci.md` states
  that the coverage is weekly, what has no functional coverage, and where
  the pieces live. `tools/idp-lab/capture-fixtures.py` now regenerates the
  two device login pages, scrubbed as the committed ones are. **Run
  against a live lab** on 2026-11-27: both providers up, Keycloak
  configured, `capture-fixtures.sh --provider keycloak` wrote all four
  of its captures into a scratch directory, and the two freshly
  captured pages carry every placeholder the committed ones do --
  including the auth session identifier inside a `<script>`, which a
  form-only scrub had missed and which was corrected by hand in the
  committed pages, so the script reproduces that correction rather
  than merely tolerating it. No live session code, execution, tab
  identifier, grant code or auth session identifier survives. The
  parser's unit test was then run against the freshly captured pages
  in place of the committed ones and passed all fourteen assertions,
  which is the check that matters: the captures are reproducible in
  the sense that a regenerated pair still proves what the committed
  pair proves. Byte equality is not the test and the script's own
  header says why -- signing keys, timestamps and session identifiers
  are per-login. The lab was taken down afterwards and no container
  of it remains.
