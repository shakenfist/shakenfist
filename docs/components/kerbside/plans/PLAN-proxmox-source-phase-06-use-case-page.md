# Proxmox source phase 6: the use-case page

This is the detailed plan for phase 6 of
[PLAN-proxmox-source.md](/components/kerbside/plans/PLAN-proxmox-source/). Read the
master plan first, and the
[phase 4](/components/kerbside/plans/PLAN-proxmox-source-phase-04-source-driver/) and
[phase 5](/components/kerbside/plans/PLAN-proxmox-source-phase-05-ci-lane/) plans, whose
driver and lane this page describes. The page format was settled
by [PLAN-use-case-docs.md](/components/kerbside/plans/PLAN-use-case-docs/), which is
complete. Its phase plans record the lesson that matters most
here: five phases running, a page stated the backend TLS leg more
strongly than the code supports.

## Prompt

All work for this phase lands in `shakenfist/kerbside`, and it is
documentation. It adds `docs/use-cases/proxmox.md`, links it from
the Use Cases table in `docs/index.md`, and registers it in
`docs/use-cases/order.yml`. It also updates the sibling pages that
enumerate the source pages, and corrects
`tools/check-backend-tls-claims.py`'s description of the backend
leg, which predates the tunnel. It changes no code path. Consult
`docs/use-cases/ovirt.md`, the closest sibling,
`docs/console-sources.md`'s Proxmox VE section,
`tools/proxmox-e2e/README.md`, and
`rust/kerbside-proxy/src/backend.rs`. Run `tox -epy3`,
`pre-commit run --all-files`, `tools/check-backend-tls-claims.py`
and `tools/mermaid-lint.sh` before every commit.

Planning effort: **medium**. The format is settled and the facts
were established by phases 4 and 5. What needs care is the claims:
every statement about the backend leg, the account and the CI
coverage has to match the code and the lane's run, not the plan
that predicted them.

## Scope

In scope:

- `docs/use-cases/proxmox.md`, in the shape of `ovirt.md`: value
  proposition, how it works, how to set it up, a worked example,
  the user interaction model, status and limitations, and see
  also.
- Its row in `docs/index.md`'s Use Cases table, and its entry in
  `docs/use-cases/order.yml`.
- The sibling pages' enumerations and *See also* lists, which name
  four source pages.
- Correcting the backend-TLS claims checker's docstring.
- Correcting one stale tier claim in `ovirt.md`, which the survey
  found while reading the sibling this page is modelled on.

Out of scope:

- Any change to the driver, the lane or the proxy.
- A second option reference. `console-sources.md` is the reference
  for `type: proxmox`, and this page links to it rather than
  restating it (decision 2).
- Changing the checker's rules or vocabulary (decision 6).
- `deploy-proxmox-on-shakenfist`'s token, which phase 5's *Future
  work* leaves to `shakenfist/actions`.

## What the survey found

1. **The index row and the page slot are waiting.**
   `docs/index.md:121` reads `| Proxmox | Deferred until a Proxmox
   source driver exists | No source yet |`. `docs/use-cases/order.yml`
   lists six pages and no `proxmox.md`. The sentence after the
   table, "Scenarios without a link are planned rather than written;
   see plans/PLAN-use-case-docs.md", becomes false once this row has
   a link, because no unlinked row will remain.

2. **The master plan's Mission is stale about this page.** It puts
   "the Proxmox use-case documentation page" out of scope, as
   belonging to PLAN-use-case-docs.md's table
   (`PLAN-proxmox-source.md:48-51`). Its own phase table makes the
   page phase 6, and PLAN-use-case-docs.md closed saying Proxmox
   stays with this plan. Corrected at source.

3. **The checker's model of the backend leg predates the tunnel.**
   `tools/check-backend-tls-claims.py`'s docstring says the proxy
   dials the insecure port first and escalates to TLS only on
   `NEED_SECURED`, and it cites `backend.rs:85-115` and `:206`. Since
   phase 3b a tunnelled target is dialled once: a plaintext HTTP
   CONNECT to the proxy, then a SPICE session that is TLS only inside
   it (`backend.rs:31-32`, `:82`, `:214-216`). A tunnel without a
   host subject is refused (`backend.rs:367`, and ryll's
   `SpiceClient::new`). So on this page the honest claim is the
   strong one: the SPICE session to qemu is always TLS and always
   pinned, though the CONNECT carrying the signed connect host is
   not. The
   checker would flag that sentence unless its block names a
   condition. Its vocabulary already has the true one: a tunnel
   `without` a subject is refused, and a VM is reached `only`
   through `spiceproxy`. The docstring and line numbers need
   correcting. The rules do not.

4. **`console-sources.md` already carries the reference**, in about
   120 lines (`## Proxmox VE` and its subsections). Those lines cover:
   - the fields, and the `pveum` commands;
   - the two ports, and which VMs are listed;
   - the uuid form;
   - the 30-second ticket and its reuse window;
   - the 409 for a direct `.vv`;
   - that a second session displaces the first.

   This is the duplication hazard PLAN-use-case-docs.md's phases met
   on every page.

5. **The lane proved what the page may claim.** Phase 5's run
   40805221046 proved, against a real PVE 9 node:
   - the documented token held exactly `VM.Audit` and `VM.Console`
     on `/vms`, and each privilege is needed;
   - discovery skipped a `std`-display, a no-SMBIOS, a stopped and a
     template guest;
   - one mint per session;
   - every backend leg tunnelled, TLS and pinned;
   - a session outliving its ticket and its token;
   - a second session displacing the first;
   - a REST terminate.

   It also proved what the page may not claim. Only one node was
   tested. The lane is nightly, and advisory on Proxmox-path pull
   requests, not a merge gate. Live migration is untested and has a
   known subject gap (phase 4 *Future work*).

6. **PVE's own console button bypasses Kerbside.** The PVE web
   interface's SPICE button calls `spiceproxy` as the logged-in user,
   which needs `VM.Console` (phase 4 survey finding 7), and hands the
   browser a `.vv` pointing at the node's `spiceproxy`. The user then
   connects without Kerbside. So "Kerbside is the only way in" holds
   only when users hold no `VM.Console`, and the page has to say so.

7. **Sibling pages count the source pages.**
   - `multi-cloud.md:182` calls them "the four source pages".
   - `multi-cloud.md:137` enumerates each source's path.
   - The *See also* lists in `ovirt.md`, `openstack.md`,
     `shakenfist.md`, `standalone.md` and `multi-cloud.md` name
     their siblings.

8. **`ovirt.md:310-311` is stale.** It says the oVirt source "is
   exercised end to end on every merge-queue entry". The oVirt lane
   has been nightly-tier since before PLAN-use-case-docs.md closed
   (`docs/testing.md`, *CI tiers*). This phase edits that page's
   *See also* anyway, so the sentence is corrected in passing rather
   than filed (decision 7).

Findings 1 and 2 are corrected in the planning commit, in the
master plan's Mission and its phase 6 row, and the `index.md` row
describes this phase from the survey.

## Decisions

1. **The page follows `ovirt.md`'s shape.** oVirt is the nearest
   sibling: a minting source whose native path is an HTTP CONNECT
   relay that cannot see into SPICE. On Proxmox that relay is
   `spiceproxy`. The value proposition is the same argument, made
   with Proxmox's facts:
   - Kerbside puts a protocol-aware front door before `spiceproxy`,
     so sessions are objects that can be listed, audited and ended;
   - the SPICE firewall applies;
   - the node's `spiceproxy` need not be reachable from users at all.

2. **The page says how it works and links for the reference.** It
   states the shape of each fact in a sentence, then links the
   matching `console-sources.md` subsection: the account, the
   network, which VMs, the uuid, tickets, and no direct consoles. It
   does not copy the `pveum` block, the field table or the numbers
   beyond the one or two the argument needs. Each of those has a
   single home, and the lane runs the `pveum` block from that home.
   This is the decision most likely to be argued with. A reader
   setting up Proxmox has to follow links where the oVirt page gave
   them the steps inline. The oVirt page predates its source
   reference growing to match, and the Proxmox reference is the one
   the CI lane executes, so a second copy would be the one that
   drifts.

3. **The backend leg is described as it is: the SPICE session is
   always TLS and always pinned, the CONNECT before it is plaintext,
   and why.** That is stronger than any sibling page can say. So the
   page gives the reason in the same block: Kerbside reaches a
   Proxmox VM only through `spiceproxy`, the session inside the
   tunnel is TLS only, and a tunnel without a node subject is
   refused. The CA is
   the source's `ca_cert`, never the CA the API returns. Every such
   sentence is checked against `backend.rs`, `proxmox.py` and the
   lane's log lines in step 6.3, not against this plan.

4. **The user interaction model says how to make Kerbside the only
   way in.** It follows `ovirt.md`'s three options: Kerbside's UI,
   your own portal on the REST API, and PVE's own console button. It
   adds the one Proxmox-specific rule: PVE's button needs
   `VM.Console`, so to route every console through Kerbside, give
   `VM.Console` to Kerbside's token and to no user.

5. **The limitations table is built from phases 4 and 5, not from
   the master plan.** Its rows:
   - one node tested, and clustering untested;
   - the lane's tier;
   - the 30-second ticket and the reuse window;
   - no direct `.vv`;
   - a second session displaces the first;
   - live migration's subject gap;
   - an API certificate from another CA (ACME) is unsupported;
   - VNC and LXC are not brokered;
   - an unreachable API stops new sessions, as on oVirt.

   Each row names its source: a plan's *Future work*, the lane run,
   or the code.

6. **The checker's docstring is corrected; its rules are not.**
   Adding `tunnel` or `tunnelled` to the condition vocabulary would
   exempt every block on this page, since nearly every block names
   the tunnel. The checker's docstring warns against exactly that.
   The page instead states its conditions in words the vocabulary
   already has (finding 3). Step 6.2 also adds two test cases: the
   page's conditioned form passes, and a bare "the backend leg is
   TLS and pinned" still fails.

7. **The stale oVirt tier claim is fixed in passing.** It is one
   sentence in a file this phase already edits, and
   `docs/testing.md` states the right tier. Filing it would cost more
   than fixing it.

8. **The index row's CI column names the lane and its tier**:
   `proxmox-functional`, nightly tier, advisory on Proxmox-path pull
   requests. The "Scenarios without a link" sentence is removed, not
   reworded, because no such scenario remains.

## Step plan

Steps 6.1 and 6.2 run in order, one sub-agent each, in this
worktree. Both run `pre-commit`. Step 6.3 is a read-only fact check
of 6.1's page, by a different model, run after 6.1 so it reads the
page as written.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6.1 | high | opus | none | **The page.** Read decisions 1-5, 7 and 8 and findings 1, 4-8. Write `docs/use-cases/proxmox.md` in `docs/use-cases/ovirt.md`'s section order and voice, with a mermaid diagram of the path (client, Kerbside's proxy, `spiceproxy`'s CONNECT, the node's qemu, with the authorize-time mint from the API). Link `console-sources.md`'s subsections (`#proxmox-ve`, `#proxmox-ve-account`, `#proxmox-ve-network`, `#which-vms-are-listed`, `#proxmox-ve-console-uuid`, `#proxmox-ve-console-tickets`, `#proxmox-ve-has-no-direct-consoles`; check each anchor exists) instead of restating them. The worked example is `.github/workflows/proxmox-functional.yml` and `tools/proxmox-e2e/`: say what it runs and what one green run proved, citing run 40805221046. Add `proxmox.md: Proxmox VE` to `docs/use-cases/order.yml` after oVirt. Link the `docs/index.md` row, with decision 8's CI column, and remove the sentence after the table. Update the sibling enumerations and *See also* lists (finding 7), and correct `ovirt.md:310-311` (finding 8) from `docs/testing.md`'s *CI tiers*. Describe the code as it is, with no references to plans, phases or decisions. Run `tools/check-backend-tls-claims.py`, `tools/mermaid-lint.sh`, `tox -epy3` (it includes the docs link tests) and `pre-commit run --all-files`. Commit: "Add the Proxmox VE use-case page." |
| 6.2 | medium | sonnet | none | **The checker's docstring.** Read decision 6 and finding 3. In `tools/check-backend-tls-claims.py`'s module docstring, describe the backend leg's two shapes with current `backend.rs` line references. A direct target is dialled insecure first and escalates on `NEED_SECURED`. A tunnelled target is dialled once, over TLS only, and refused without a host subject. Say why `tunnel` is not a condition word. Do not change any regex. In `kerbside/tests/unit/test_check_backend_tls_claims.py`, add a case that the page's conditioned tunnel sentence passes and a case that an unconditioned backend TLS-and-pinned sentence fails, each mirroring the existing tests' style. Run the tool's mutation tester, `tools/mutate-backend-tls-claims.py`, and report that it still finds no untested rule. Commit: "Describe tunnelled targets in the TLS claims check." |
| 6.3 | high | sonnet | none | **Fact check, read only.** Read `docs/use-cases/proxmox.md` and list every factual claim it makes about ports, privileges, tickets, the backend leg, discovery, the user interaction model and CI coverage. For each, give the line, a verdict of supported, overstated, wrong or unverifiable, and the evidence: a file and line in `kerbside/`, `rust/kerbside-proxy/src/`, `tools/proxmox-e2e/` or `.github/workflows/proxmox-functional.yml`, or a phrase in run 40805221046's log (read with `ci-status shakenfist/kerbside logs 123222439809`). Treat any claim about the backend leg as overstated unless the code shows it. Change nothing. The management session fixes what it finds, in a commit of its own, "Correct the Proxmox page's claims.", if anything changes. |

## Risks and mitigations

- **The page overstates the backend leg.** Every sibling did at
  least once. Mitigation: decision 3, the checker, and step 6.3's
  claim-by-claim check against code. The management session reads
  every sentence containing TLS, pin, verify or subject before
  committing.
- **The page drifts from `console-sources.md`.** Mitigation:
  decision 2 keeps one home for each fact. Step 6.3 flags any number
  or command that appears on both pages.
- **A broken anchor.** The page links eight subsections by
  generated anchor. Mitigation: `tox -epy3` runs the docs link tests.
  Step 6.1 checks each anchor exists. Note that issue #490 records
  that the link test strips anchors from relative links, so step 6.1
  checks them by hand.
- **The checker change weakens it.** Mitigation: decision 6 changes
  no rule, and step 6.2 runs the mutation tester.

## Definition of done

- [x] `docs/use-cases/proxmox.md` exists, has the seven section
      headings of `ovirt.md` in the same order, and contains a
      mermaid diagram that `tools/mermaid-lint.sh` accepts.
- [x] `docs/index.md`'s Proxmox row links the page and names
      `proxmox-functional`. No Use Cases row is left without a link,
      and the "Scenarios without a link" sentence is gone.
- [x] `docs/use-cases/order.yml` lists `proxmox.md`.
- [x] The page contains no `pveum` command and no `sources.yaml`
      field table. `grep -c 'pveum ' docs/use-cases/proxmox.md` is 0,
      and each anchor it links in `console-sources.md` exists.
- [x] No sibling page still says "four source pages" or omits
      Proxmox from a list of the source pages.
- [x] `ovirt.md` no longer says the oVirt lane runs on every
      merge-queue entry.
- [x] `tools/check-backend-tls-claims.py` passes. Its docstring
      describes tunnelled targets with current line numbers, and its
      two new test cases fail if the conditioned sentence loses its
      condition word.
- [x] Step 6.3's fact check found no claim overstated or wrong, or
      each one it found is corrected, with the correction listed in
      *Execution notes*.
- [x] `tox -epy3`, `tox -eflake8`, `pre-commit run --all-files` and
      `tools/mermaid-lint.sh` pass.
- [x] The master plan's Execution table records this phase's merge
      commit, set by the close-out commit of the push-audit phase's
      planning.

## Bugs fixed during this work

None yet.

## Future work

- Once `deploy-proxmox-on-shakenfist` creates the documented
  account (phase 5 *Future work*), the page's worked example can
  say the CI node's own token is least-privilege too.

## Execution notes

- 2026-10-29 AEDT, back brief. Mikal confirmed decision 2 (the page
  links `console-sources.md` for setup details rather than restating
  them) and that this session may push the branch and open the pull
  request once the definition of done passes locally.
- Steps run in sequence, 6.1 to 6.3: 6.1 and 6.2 both run
  `pre-commit` in the shared worktree, and 6.3 reads 6.1's page.
- Step 6.1 also added Proxmox VE to the README's Use Cases link and
  to `docs/installation.md`'s table of deployment guides, which both
  enumerate the pages. The README addition is kept. The installation
  table was gone after the rebase onto deployment options phase 6,
  which replaced it with a pointer to the Use Cases index, so that
  addition was dropped. 6.1 left `console-sources.md` unedited.
- Step 6.2's tunnel sentence said a tunnelled target is dialled "over
  TLS only". The management session reworded it before committing:
  the CONNECT to the proxy is plaintext, and only the SPICE session
  inside the tunnel is TLS. 6.2's brief in the step table has the
  same imprecision and is left as written, because it records what
  the brief said. Finding 3 and decision 3 had it too, and are
  corrected at their source after the first review of the pull
  request, as is this phase's row in `docs/plans/index.md`.
- 2026-10-30 AEDT, step 6.3. The fact check found nothing that
  contradicts `console-sources.md`, but some claims went further
  than the code or Proxmox supports. Corrected:
  1. The backend leg. The hop to `spiceproxy` is a plaintext HTTP
     CONNECT, and the signed connect host crosses that network in the
     clear; only the SPICE session inside the tunnel is TLS. This is
     fixed in the value bullet, the mermaid labels and the backend
     leg paragraph. The same imprecision was swept from
     `docs/network-ports.md`'s tunnel sentence and the
     `docs/index.md` row.
  2. The account commands. The lane also adds `--output-format
     json`, not only a renamed user.
  3. `spiceproxy` setup. It honours `/etc/default/pveproxy`'s
     `ALLOW_FROM`, `DENY_FROM`, `POLICY` and `LISTEN_IP` on port
     3128, which an address restriction must account for.
  4. The `.vv` from PVE's console button names the host in the
     browser's address bar, not "the node's" `spiceproxy`.
  5. Who keeps that button. `PVEVMUser`, `PVEVMAdmin`, `PVEAdmin`,
     `Administrator` and `root@pam` all hold `VM.Console`, not only
     "administrators".
  6. Another minter changes the password a Kerbside session's later
     channels expect (see the refusal comment in `kerbside/api.py`'s
     direct console handler). This is now stated, where the page had
     said a mint "leaves clients already connected alone".
  7. "Whoever opened it" is dropped as unverifiable.
  8. "Only Kerbside can make the CONNECT" overstated what is a
     network design choice. The reason given for no direct consoles
     is now the password change, as `api.py` gives it.
  9. The CI runner is self-hosted, not GitHub-hosted. Only Kerbside
     is limited to 8006 and 3128; the lane's scripts reach the node
     separately.
  10. Pull requests from forks skip the lane. The live migration
      gap is "expected from the code", not "known".
  Declined, because ovirt.md shares the claims and they are not
  Proxmox-specific: the SPICE features paragraph, and the firewall
  "classifies every framed message". Kept, as decision 2 allows:
  the few numbers the page repeats from `console-sources.md` (30
  seconds, the ports, the two privileges).
- The definition of done's mutant for the new tests had to remove
  every condition word in the fixture, not one. The bullet's heading
  holds its own `only`, and the checker works per block. That is the
  checker's documented limit, not a gap in the tests.

## Outcome

The definition of done was run locally on 2026-10-30 AEDT, at the
head of `proxmox-source-phase-06-use-case-page`:

- `proxmox.md` has `ovirt.md`'s seven section headings in order
  (`diff` of the `## ` lines is empty) and one mermaid diagram,
  which `tools/mermaid-lint.sh` accepts.
- Every Use Cases row in `docs/index.md` links a page. The Proxmox
  row names `proxmox-functional`, and "Scenarios without a link" no
  longer appears.
- `order.yml` lists `proxmox.md`.
- `grep -c 'pveum ' docs/use-cases/proxmox.md` is 0, there is no
  field table, and all 8 `console-sources.md` anchors the page links
  exist: the seven setup and behaviour subsections, and
  `#example-sourcesyaml`.
- "four source pages" appears only in this plan, and `ovirt.md`
  no longer mentions the merge queue.
- `tools/check-backend-tls-claims.py` passes over 12 files. Its 21
  tests pass, and `tools/mutate-backend-tls-claims.py` catches all
  12 of its mutations. A mutant that strips the condition words
  from the new tunnel fixture fails
  `test_a_tunnel_page_naming_its_condition_passes`.
- Step 6.3's corrections are listed in *Execution notes*.
- `tox -epy3` (801 passed), `tox -eflake8`, `pre-commit run
  --all-files` and `tools/mermaid-lint.sh` pass.

The last item, this phase's merge commit, is recorded by the
push-audit phase's planning close-out, so it is unticked here.

## Back brief

Before executing any step of this plan, back brief the operator
on the intended approach and on any deviation from it. One point
needs an answer before step 6.1 starts:

1. **Decision 2, link rather than restate.** The page explains how
   Proxmox works with Kerbside and links `console-sources.md` for
   every setup detail, rather than giving the `pveum` commands and
   field table inline as `ovirt.md` does.

Also confirm whether this session may push the branch and open the
pull request once the definition of done is met locally.
