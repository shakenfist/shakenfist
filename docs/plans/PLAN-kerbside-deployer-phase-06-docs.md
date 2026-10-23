# Phase 6 -- Documentation and close-out

Part of [PLAN-kerbside-deployer.md](PLAN-kerbside-deployer.md). Phase 5
([PLAN-kerbside-deployer-phase-05-role-to-kerbside.md](PLAN-kerbside-deployer-phase-05-role-to-kerbside.md))
landed in #4591 as `48849831c`, with kerbside#580 and #587 and
actions#181 and #195. This phase's first commit closes it out.

**Planning effort:** medium, as the master plan asks. The phase writes
documentation in two repositories and changes no code.
**Review effort:** high for step 1, whose topology and load-balancer
claims are about behaviour no CI lane exercises. Medium elsewhere.

## Scope

**In scope:**

* A new `docs/operator_guide/kerbside.md`. It is the Shaken Fist
  operator's page for running Kerbside beside a cluster:
  * the topologies;
  * the network each one needs;
  * load balancing the API and the SPICE ports;
  * certificates;
  * what the integration does not do yet.

  It links to the variable references rather than repeating them (D1).
* Fixes to `vdi_console_tokens.md`: the "admin callers" error, and a
  cross-reference to a section the page no longer has (D3).
* The site navigation. `vdi_console_tokens.md` has never been in it,
  and the new page joins it (D4).
* `ARCHITECTURE.md` names Kerbside as an optional deployed component,
  as the master plan's success criteria require (D5).
* Pointers to the new page from `installation.md`, the collection
  README and the release note (D6).
* In Kerbside, the claim that the API and the daemon must be
  co-located is corrected everywhere it appears. `use-cases/shakenfist.md`
  points at the collection as the supported deploy path (D7).
* Closing kerbside#579, whose own close condition has been met (D8).

**Out of scope, and why:**

* **Kerbside's full Ansible page** (`docs/deployment/ansible.md`).
  Kerbside's deployment options plan, phase 5, owns it. This phase
  edits Kerbside's existing pages only to make them true.
* **Testing more than one Kerbside host.** The master plan's Future
  work keeps it. The load-balancer section says plainly that the shape
  is untested (D2).
* **The two other operator pages missing from the navigation,**
  `capacity_refusals.md` and `credential_rotation.md`. They are
  unrelated to Kerbside. Step 2 adds them anyway, because it edits
  the same block of the same file, and an issue would cost more than
  the fix (D4).
* **The `pip_extra` gap in the node role,** found in phase 5. It
  predates this plan and is filed as #4614.
* **Publishing `shakenfist.shakenfist` to Galaxy.** Filed as #4613.

## What the survey found

The master plan's phase 6 section was written before phases 3 to 5
ran, and much of it has since been done by them.

### S1 -- `vdi_console_tokens.md` already lost its manual step

* **Gone:** the "manual step" note the master plan wanted removed. It
  was `0ef587154d:docs/operator_guide/vdi_console_tokens.md:58`, and
  phases 3 and 5 rewrote the page (`3af60ce3f6`, `46eca7110a`).
* **Already there:** the page names the variables, the group and the
  path for a Kerbside you deploy yourself (`:55-91`).
* **Still wrong: "admin callers".** It has moved from `:172` to
  `:240`.
  * `AdminVDITokenPublicKeyEndpoint.get`
    (`shakenfist/external_api/admin.py:78-90`) carries only
    `@api_base.log_token_use`. Unlike its neighbours at `:35` and
    `:114`, it has no `@api_base.caller_is_admin`.
  * Authentication is the default for every `Resource`
    (`base.py:2577-2590`).
  * So any authenticated caller can read the key. The key is public,
    so this is a documentation error and not a disclosure.
* **Newly stale.** `:80-82` says "the manual Kerbside setup described
  later on this page is then only for a Kerbside you deploy some other
  way". The page no longer describes any Kerbside setup. What follows
  is the Shaken Fist side: the signing key, the credential and
  rotation.

### S2 -- `installation.md` already documents the group and variables

* The master plan asked `installation.md` to document the group and
  the variables. Phases 3 and 5 did that, at `:211-216` and
  `:219-270`.
* The page also covers the database Kerbside needs, with the SQL to
  create it (`:253-261`). That was on the master plan's list for the
  new page.

Neither needs writing again. The page needs a pointer to `kerbside.md`
(D6).

### S3 -- three pages already state Kerbside's variables

* **Shaken Fist's five,** at `installation.md:211-216` and at the
  collection README's "Deploying Kerbside" table
  (`shakenfist/deploy/collection/README.md`, in `## Kerbside`).
* **The role's own,** in Kerbside's `ansible/README.md`. Its sections
  cover ports (`:158`), certificates (`:185`, including the
  three-path override, all or none), readiness, layout, and what a
  redeploy restarts (`:225`).

A fourth table would drift, so the new page links to these (D1).

### S4 -- `docs/operator_guide/kerbside.md` does not exist

Nothing else has the topics the master plan listed for it:
* how the topologies compare;
* the load-balancer shape;
* the costs of having no Keystone (kerbside#300, open) and no pinned
  CA (kerbside#136, open).

`docs/operator_guide/load_balancing.md` covers the Shaken Fist API,
with Apache, nginx and HAProxy examples. The new page follows its
shape. `examples/nginx-loadbalancer.conf` exists.

### S5 -- the operator guide navigation misses three pages

`mkdocs.yml` is generated by the automated documentation sync, from
`mkdocs.yml.tmpl` (`:128-145` holds the Operator Guide). These pages
are under `docs/operator_guide/` but not in the template:
* `vdi_console_tokens.md`;
* `capacity_refusals.md`;
* `credential_rotation.md`.

None of the three is reachable from the site's navigation.

### S6 -- `ARCHITECTURE.md` does not name Kerbside as a component

Its only mention is at `:320-323`, under Configuration: the
integration's settings and the signing key. "System Components"
(`:37`) lists the `sf-*` daemons only. The master plan's success
criteria require Kerbside there, as an optional deployed component.

### S7 -- the release note already has its Kerbside bullets

`docs/release_notes/v07-v08.md:66-105` has two bullets. One covers
the Shaken Fist side. The other covers deploying Kerbside, and phase 5
updated it. No Shaken Fist release has been tagged since, so the note
is still the current one. It needs only the new page's link.

### S8 -- Kerbside's co-location claim is wrong in four places

In Kerbside's code, only the daemon and its Rust proxy use
`API_SOCKET_PATH`:
* `kerbside/main.py:19` serves it, through `kerbside/rpc/server.py`;
* `kerbside/proxy_supervisor.py:189` hands it to the proxy;
* nothing under `kerbside/api*` dials it.

The API reaches the daemon only through the database. For example,
termination is `db.request_session_termination`, which the daemon
reaps (`kerbside/db.py:760`, `kerbside/main.py:409`).

These pages are wrong, on Kerbside `origin/develop` at `829c636`:
* `docs/installation.md:86-100`, "Two processes, co-located ... they
  must be co-located";
* `docs/installation.md:317`, "Real deployments run them as separate
  units sharing the socket";
* `docs/network-ports.md:16`, "it is why the API, daemon and proxy
  must be co-located";
* `demo/README.md:61-65`, "The REST API and the SPICE proxy daemon
  share a unix socket".

These are right and stay as they are:
* `docs/configuration.md:91`, "The proxy must be co-located with the
  daemon";
* `demo/entrypoint.sh:140`.

### S9 -- Kerbside's Shaken Fist use case predates the collection

`docs/use-cases/shakenfist.md` "How to set it up" (`:173-282`)
describes only the manual path: a hand-written `sources.yaml` and a
signing key created by hand ("Creating the key is an explicit operator
step"). Its worked example (`:283-317`) already deploys through the
role. Its limitations table says "Tracked as #444", but kerbside#444
was closed on 2026-10-17 by kerbside#569, a docs change. The
limitation is still real.

### S10 -- issue states have moved

| Issue | Master plan said | Now |
|-------|------------------|-----|
| #4415, #4093, #4459 | open | closed |
| kerbside#131, #313, #465 | open | closed |
| kerbside#579 | open, nightly failing | open, but the nightly has passed on 2026-10-19, -20, -21 and -22. Its body says to close it "when the nightly is green again" |
| kerbside#444 | cited, as if open, among the needs of a least-privilege scrape credential | closed on 2026-10-17 by kerbside#569, a documentation change; the limitation it describes still holds |
| #4097 | item 4 open | item 4's Kerbside side was commented done in phase 5; the issue stays open for its other items |

### Corrections made at source

The planning commit corrects the master plan:
* the phase 6 section, against S1, S2, S6 and S7;
* the Bugs fixed statuses, against S10;
* the least-privilege Future work entry, which now says how
  kerbside#444 was closed.

The `index.md` row describes what this survey found. A later step
need not redo any of this.

## Decisions

### D1 -- `kerbside.md` is the narrative, and the variable tables stay where they are

The new page answers questions that no page answers today:
* which topology to choose;
* what each topology needs from the network;
* how to put more than one Kerbside host behind a load balancer;
* where the certificates come from;
* what you give up.

It links to `installation.md` for Shaken Fist's variables and the
database, and to Kerbside's `ansible/README.md` for the role's
variables. It repeats neither.

The master plan listed "the database to create" for this page.
`installation.md` already has it, as an install step beside the
variables it needs, so the new page links there. Moving it would leave
the installation guide with a hole in the middle of its Kerbside
section.

### D2 -- the load-balancer section describes an untested shape, and says so

No lane runs two Kerbsides. Kerbside's `docs/use-cases/placement.md:168`
says so, and the master plan's Future work keeps "More than one
Kerbside host, tested". The master plan still asks for the shape, and
operators will try it. So the page documents it, with an admonition
that CI has not tested it.

Every claim in the section is checked against Kerbside's code or
`site.yml`, and the step's report cites a file and line for each:
* which state the hosts share (the database), and which they must
  agree on (`kerbside_auth_secret_seed`, `kerbside_url`,
  `kerbside_public_fqdn`);
* whether a token exchanged through one host's API can be redeemed at
  another host's proxy. This decides whether the SPICE leg needs
  session affinity;
* that the SPICE ports need TCP passthrough rather than TLS
  termination, because the console file carries the proxy's own CA;
* whose certificate each host presents, and with what name.

**If the code does not support redeeming a session at another host,**
the page says to keep one Kerbside host, or to pin by client address,
and the step reports it rather than inventing a shape.

The nginx example sits on the page, beside a `stream` block for 5900
and 5901. It does not go into `examples/`, where a file reads as a
supported configuration.

### D3 -- `vdi_console_tokens.md` gets two corrections and a pointer

* "Served to admin callers" becomes "served to any authenticated
  caller" (S1). The key is public by design, and the page already says
  why rotation works.
* `:80-82` stops promising a manual Kerbside setup "later on this
  page". It points at `kerbside.md` for a Kerbside you deploy yourself,
  and at Kerbside's own docs for configuring a source by hand.

Nothing else on the page changes. It is the Shaken Fist side's
reference, and phases 2 to 5 kept it current.

### D4 -- three pages join the navigation, `kerbside.md` among them

`mkdocs.yml.tmpl` gains `kerbside.md` and `vdi_console_tokens.md`, in
its alphabetical Operator Guide list. It also gains the two unrelated
orphans, `capacity_refusals.md` and `credential_rotation.md`.

**This is the decision most likely to be questioned,** as scope creep.
It is two lines in the block this step is already editing. The step
already checks that every operator guide page is in the navigation,
so including them is what makes that check true. An issue would cost
more than the fix.

`mkdocs.yml` itself is never edited. The documentation sync generates
it.

### D5 -- `ARCHITECTURE.md` gains a short optional-components entry

Under "System Components", after "Daemons", a few sentences say:
* Kerbside is a separate project and an optional component;
* `site.yml` deploys it onto the `kerbside` group, through Kerbside's
  `shakenfist.kerbside` collection;
* `sf-api` mints console tokens, which Kerbside exchanges, and Kerbside
  scrapes the cluster with its own `system`-namespace key.

It links to `kerbside.md` and `vdi_console_tokens.md`, and the
Configuration mention at `:320` stays. `AGENTS.md` does not change,
because no convention does.

### D6 -- pointers, not rewrites, everywhere else

* `installation.md`'s Kerbside section links to `kerbside.md` for
  topology and load balancing.
* The collection README's "Deploying Kerbside" links to it.
* The release note's deploy-Kerbside bullet links to it.

None of them gains new prose beyond the link sentence.

### D7 -- Kerbside's pages are corrected in Kerbside, in one pull request

* **The co-location claim.** Correct all four sites in S8 to the same
  statement: the daemon and its proxy must share a host, because they
  share `API_SOCKET_PATH`. The API may run elsewhere, since it reaches
  the daemon only through the database.
* **`installation.md:86-100`'s heading.** It says "co-located", and
  becomes accurate rather than being deleted. Kerbside's deployment
  options plan, phase 6, rewrites the "Deploying for real" section
  further down. This edit stays clear of it.
* **`use-cases/shakenfist.md`.** "How to set it up" opens by naming
  the supported path: Shaken Fist's `site.yml`, with hosts in its
  `kerbside` group, deploys Kerbside through `shakenfist.kerbside`, and
  does each step below for you. The steps stay, as what that deploy
  does and as the path for a Kerbside deployed another way. The
  signing-key sentence says that the collection creates the key. The
  limitations row's "Tracked as #444" is corrected (S9).
* **Never in Shaken Fist's `docs/components/kerbside/`.** The
  documentation sync carries Kerbside's pages here.

The Kerbside pull request needs nothing from Shaken Fist's, and can
merge in either order.

### D8 -- kerbside#579 is closed with a comment

Its body says to close it when the nightly is green again. Four
consecutive nightlies have passed:
* [39219408414](https://github.com/shakenfist/kerbside/actions/runs/39219408414);
* [39388916124](https://github.com/shakenfist/kerbside/actions/runs/39388916124);
* [39564983702](https://github.com/shakenfist/kerbside/actions/runs/39564983702);
* [39743016396](https://github.com/shakenfist/kerbside/actions/runs/39743016396),
  the first through the role.

The management session closes it, after the back brief confirms it.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | high | opus | this worktree | Write `docs/operator_guide/kerbside.md` (D1, D2). See brief 1. Commit: "Add an operator guide page for Kerbside." |
| 2 | medium | sonnet | this worktree, after step 1 | The corrections and pointers (D3-D6). See brief 2. Commit: "Link the Kerbside page and fix two doc errors." |
| 3 | medium | sonnet | Kerbside worktree `../kerbside-wt-kd-p6` from `origin/develop` | Kerbside's pages (D7). See brief 3. Commit (Kerbside): "Correct the co-location claim and SF setup." |

Steps 1 and 3 touch different repositories and can run in parallel.
Step 2 links to step 1's page and anchors, so it runs after step 1.
The management session runs each repository's
`pre-commit run --all-files` and reviews each step before committing
it. It then closes kerbside#579 (D8), and records the step results in
*Execution notes*.

There are two pull requests: this branch (steps 1 and 2, with the
planning commits), and Kerbside's (step 3).

### Brief 1 -- the operator page

This worktree. Read D1 and D2, and S1-S4.

**The audience:** a Shaken Fist operator who has read `installation.md`
and wants to know how to place Kerbside.

**Sections**, adjusted as the content needs:
* **What Kerbside is here.** One paragraph, linking to
  `vdi_console_tokens.md` for the token flow, and to
  `/components/kerbside/` for Kerbside itself.
* **Topologies.**
  * Co-located on a hypervisor.
  * A dedicated host.
  * Bring your own: `kerbside_url` with an empty or absent group.
  * One Kerbside serving several clusters or platforms, through
    `kerbside_extra_sources`.

  Say what each needs. A dedicated host needs a non-loopback
  `api_url`. Ports are reserved on a co-located host. A Kerbside you
  bring yourself must be given this cluster's source by hand. Take
  these from `site.yml`'s header comment (`examples/_shared/site.yml:19-69`)
  and the collection README's `## Kerbside`.
* **The network.** Kerbside needs routes to:
  * every hypervisor's VDI ports;
  * `api_url`;
  * its database.

  Users need routes only to the Shaken Fist API and to
  `kerbside_public_fqdn`. Kerbside's
  `docs/use-cases/shakenfist.md` "Network" section has this, so
  link it rather than restating its reasoning.
* **Load balancing (D2).**
  * An admonition that CI does not test more than one Kerbside host.
  * The API leg is HTTP, with an nginx `http` example in the style of
    `docs/operator_guide/load_balancing.md`.
  * The SPICE leg is TCP passthrough of the secure and insecure
    ports, with an nginx `stream` example.
  * Verify each claim D2 lists against Kerbside's code, in
    `../kerbside` (read it, never modify it). Answer redemption at
    another host first. **If it is not supported, stop and report
    before writing the section.**
* **Certificates.**
  * How `site.yml` issues each Kerbside host's proxy certificate
    through `internal_ca`. Find the play, and state the subject and
    SANs it actually requests.
  * That the console file embeds the CA.
  * Bringing your own certificate with the three path variables (all
    or none). Link Kerbside's `ansible/README.md` "Certificates"
    section for the variables.
  * What changes behind a load balancer: every host must present a
    certificate valid for `kerbside_public_fqdn`.
* **What you give up today.**
  * No admin login without Keystone (kerbside#300). Terminating a
    session needs a JWT minted from the seed (kerbside#301).
  * Kerbside's Shaken Fist client pins no CA (kerbside#136), so an
    `api_url` behind a private CA needs that CA in the Kerbside host's
    trust store.
  * The scrape credential is a full `system` key, because no scoped
    key exists yet.

  Check each issue's state with `gh issue view` before citing it.

**Constraints:**
* No plan, phase or decision citations in the page. The
  `check-plan-phase-references` hook enforces this.
* Site-absolute links to other docs pages follow the pattern
  `vdi_console_tokens.md` uses (`/components/kerbside/...`), and
  relative links within `operator_guide/`.
* Wrap prose at 79 characters as the neighbouring pages do. Lead with
  the answer in each section.
* Never edit `docs/components/kerbside/`.
* Do not add the page to the navigation. Step 2 does that.

**Checks:** `pre-commit run --all-files` after `git add -N` on the new
file. `check-doc-anchors` must pass.

**Report** (under 400 words): the sections written; a file:line
citation for each D2 claim; the certificate subject and SANs found;
anything you could not verify. Leave the work uncommitted.

### Brief 2 -- corrections and pointers

This worktree, after step 1's page exists. Read D3-D6 and S1, S2, S5,
S6 and S7.

* **`docs/operator_guide/vdi_console_tokens.md`:**
  * `:240`: "admin callers" becomes "any authenticated caller".
  * `:80-82`: replace the promise of a manual setup "later on this
    page" with a pointer to `kerbside.md`, and to Kerbside's
    console-sources page for configuring a source by hand.
  * Add `kerbside.md` to "Related pages" (`:341`).
* **`mkdocs.yml.tmpl`**, Operator Guide (`:128-145`): add
  `"Kerbside": operator_guide/kerbside.md`,
  `"VDI Console Tokens": operator_guide/vdi_console_tokens.md`,
  `"Capacity Refusals": operator_guide/capacity_refusals.md` and
  `"Credential Rotation": operator_guide/credential_rotation.md`,
  keeping the list's order (Installation first, then alphabetical).
  **Never edit `mkdocs.yml`.**
* **`ARCHITECTURE.md`:** an "Optional components" subsection after
  "Daemons" (`:39-53`), per D5, at most eight lines.
* **Pointers, one sentence each (D6):**
  * `docs/operator_guide/installation.md`'s Kerbside section, near
    `:228`;
  * `shakenfist/deploy/collection/README.md`'s "Deploying Kerbside",
    as a GitHub URL to `docs/operator_guide/kerbside.md`, since that
    README is also rendered outside the site;
  * `docs/release_notes/v07-v08.md`'s deploy-Kerbside bullet
    (`:85-105`).

**Constraints:** the same as brief 1's. Change no other prose.

**Checks:** `pre-commit run --all-files`, and the DoD scripts for this
step (below).

**Report** (under 200 words): files changed and the check output.
Leave the work uncommitted.

### Brief 3 -- Kerbside's pages

A new Kerbside worktree, `../kerbside-wt-kd-p6` on a branch
`kerbside-deployer-docs` from `origin/develop`. Read D7, S8 and S9.

* **The co-location claim, at all four S8 sites.** State it the same
  way each time: the daemon and its proxy must share a host, because
  they share `API_SOCKET_PATH`. The API may run elsewhere, because it
  reaches the daemon only through the database.
  * In `docs/installation.md:86-100`, retitle "Two processes,
    co-located" to say what is actually true.
  * Correct the table row at `:317`.
  * Correct `docs/network-ports.md:16`'s reason.
  * Correct `demo/README.md:61-65`. The demo still runs all three in
    one container, and that stays.

  Before editing, re-verify S8's code claims yourself: grep for every
  reader of `API_SOCKET_PATH` and every gRPC client. **If the API
  dials the socket anywhere, stop and report.**
* **Sweep the class.** Search `docs/`, `demo/`, `README.md`,
  `ARCHITECTURE.md` and `ansible/` for other statements that the API
  and the daemon must share a host, and fix those too. Leave
  `docs/plans/` alone. Plans are history.
* **`docs/use-cases/shakenfist.md` "How to set it up" (`:173`):**
  * open with the supported path, per D7;
  * qualify the signing-key sentence;
  * keep the manual steps as what the deploy does, and as the path for
    a Kerbside deployed another way.

  Link Shaken Fist's operator guide as
  `https://shakenfist.com/operator_guide/kerbside/`. The site is
  `mkdocs.yml.tmpl`'s `site_url`, and Kerbside's docs already link
  `https://shakenfist.com/components/ryll/` in that form.
* **The limitations row** that cites #444: drop the closed issue
  number, and keep the limitation.
* **Do not create `docs/deployment/`.** Kerbside's deployment options
  plan owns it.

**Checks:** Kerbside's `pre-commit run --all-files`. That includes
`docs-line-references`, which checks `file:line` references in docs.

**Report** (under 250 words): each site changed, the verification
grep's output summarised, and anything else the sweep found. Leave the
work uncommitted.

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| The load-balancer section asserts behaviour that no test covers, and an operator builds on it. | D2: an untested admonition, and a code citation for every claim. The management session re-reads each cited line before committing step 1, and checks most closely whether a token exchanged at one host can be redeemed at another. |
| The co-location correction is itself wrong. For example, the API might dial the socket through a path the grep missed. | Brief 3 re-verifies before editing, and stops if it finds a client. The management session repeats the grep for gRPC clients over `kerbside/`. |
| Kerbside's deployment options plan rewrites `installation.md` later and reintroduces the claim. | Its phase 6 sits after this phase's merge. The Kerbside pull request description names the corrected claim, so that phase's survey sees it. |
| A navigation entry breaks the site build, which this repository does not run. | `check-doc-anchors` covers anchors. The management session checks that each nav path names a file that exists, with the DoD script. |
| The sync overwrites `mkdocs.yml` and loses the new entries. | They go only into `mkdocs.yml.tmpl`, the sync's source. |

## Definition of done

* `docs/operator_guide/kerbside.md` exists. It has sections for
  topologies, the network, load balancing, certificates, and the
  costs, and its load-balancing section carries an admonition that it
  is untested.
* Each load-balancer claim in step 1's report has a file:line
  citation, and the management session has re-read every one.
* `grep -n "admin callers" docs/operator_guide/vdi_console_tokens.md`
  finds nothing.
* `grep -n "described later on" docs/operator_guide/vdi_console_tokens.md`
  finds nothing.
* Every operator guide page is in the navigation. This prints nothing:

  ```bash
  for f in $(git ls-files 'docs/operator_guide/*.md' | sed 's#^docs/##'); do
    grep -q "$f" mkdocs.yml.tmpl || echo "not in nav: $f"
  done
  ```

* Every navigation entry names a file that exists. This prints
  nothing:

  ```bash
  grep -o 'operator_guide/[^" ]*\.md' mkdocs.yml.tmpl | \
    while read f; do [ -f "docs/$f" ] || echo "missing: $f"; done
  ```

* `git diff origin/develop -- mkdocs.yml docs/components` is empty.
* `ARCHITECTURE.md`'s System Components names Kerbside:
  `sed -n '/^## System Components/,/^## Instance Scheduling/p' ARCHITECTURE.md | grep -c Kerbside`
  is at least 1.
* `installation.md`, the collection README and `v07-v08.md` each link
  `kerbside.md`:
  `grep -l 'kerbside.md' docs/operator_guide/installation.md shakenfist/deploy/collection/README.md docs/release_notes/v07-v08.md`
  lists all three.
* **In Kerbside,** on the step 3 branch, this finds only
  `docs/plans/` and statements that the daemon and proxy must share a
  host:

  ```bash
  git grep -n -i -E 'co-?locat|share.*(socket|API_SOCKET_PATH)' -- \
    docs demo README.md ARCHITECTURE.md ansible
  ```

  This finds no line that says the API must share a host with the
  daemon:

  ```bash
  git grep -n -i -E 'API.{0,40}(must|need).{0,40}(co-?locat|same host)' -- docs demo
  ```

* **In Kerbside,** `docs/use-cases/shakenfist.md` names
  `shakenfist.kerbside` in "How to set it up", and no longer says
  "Tracked as #444".
* kerbside#579 is closed, with a comment linking the four nightly runs.
* Both repositories' `pre-commit run --all-files` pass.

## Back brief

Before step 1, the implementing session confirms its understanding of
D1-D8 in a short message to the operator. It flags anything in this
plan that the tree contradicts. The operator answers:

1. **D4's two unrelated navigation entries:** keep them in, or leave
   them for a separate change.
2. **D8:** whether this session may close kerbside#579.
3. **The pull requests:** whether this session may push and open the
   Shaken Fist and Kerbside pull requests.

A gate sits inside step 1: if Kerbside's code cannot redeem a session
at a host other than the one that exchanged it, step 1 stops. The
operator then chooses between documenting a single Kerbside host only
and documenting affinity.

## Execution notes

**Back brief (2026-10-23, Australia/Sydney).** The operator:
* kept D4's two unrelated navigation entries;
* allowed this session to close kerbside#579;
* allowed this session to push and open both pull requests.

**D8.** kerbside#579 was closed with a comment linking the four green
nightlies.

**Steps 1 and 3 ran in parallel, then step 2.** Step 2 links step 1's
anchors, so it waited for step 1.

**Step 1, `39ab2f2415`.** D2's gate passed. The exchange stores the
console's session token in Kerbside's database. The proxy authorises
every channel through `AuthorizeConnection`, which calls
`db.get_token_by_token` (`kerbside/rpc/servicer.py:93-96`,
`kerbside/db.py:551-560`). That filters on the token and its expiry
alone, so a console file from one host is redeemable at any host that
shares the database. The management session re-read these claims:
* the redemption path above;
* that gunicorn serves plain HTTP, with no `--certfile` in the role's
  unit;
* that nothing in Kerbside reads the PROXY protocol;
* the certificate request at `site.yml:981-1013`.

It corrected one row of the network table: users exchange tokens at
`kerbside_url`, not at `kerbside_public_fqdn`'s API port.

The agent found one thing the plan had not. The deploy allows
`api_url` to differ between Kerbside hosts, and hosts that differ
rewrite the shared source row on every 60-second pass. The page tells
operators to give every host the same routable `api_url`.

It also flagged `PUBLIC_FQDN`'s description in Kerbside's
`config.py:134`, "could be a load balancer with backend affinity". That
is permissive rather than wrong, so it was left alone.

**Step 2, `527ab15bd2`.** The management session moved the collection
README's pointer to the end of the paragraph on the role's variables,
and made the release note's link relative, like its others.

**Step 3, Kerbside `a0dea40`,** on branch `kerbside-deployer-docs`.
* The verification grep found no gRPC client outside the tests.
* The class sweep found no wrong co-location claim beyond S8's four.
* The management session rewrapped the edited paragraphs and
  shortened the `installation.md` heading to "Two processes."

**Definition of done.** Every item passed on these commits. The second
Kerbside grep in the Definition of done still matches
`demo/entrypoint.sh:140`. That line is the correct daemon-and-proxy
statement, which the pattern catches because `API_SOCKET_PATH`
contains "API", so the pattern was written too broadly.

## Future work

None new. The master plan's Future work carries the testing of more
than one Kerbside host, a least-privilege scrape credential, and the
pinned-CA fix.
