# Kerbside VDI tokens phase 8: documentation

This is phase 8 of a **cross-repository master plan** whose plan of record
lives in the Shaken Fist repository
(`shakenfist/docs/plans/PLAN-kerbside-vdi-tokens.md`, branch
`vdi-console-tokens`). Phase 8 documents the whole VDI console token flow
for users, operators, and API consumers. It spans **four repos**, each doc
change landing on that repo's existing feature branch:

| Repo | Worktree | Branch |
|------|----------|--------|
| shakenfist | `shakenfist-wt-vdi-tokens` | `vdi-console-tokens` |
| kerbside | `kerbside-wt-vdi-tokens` | `sf-vdi-tokens` |
| client-python | `client-python-wt-vdi-tokens` | `vdi-console-tokens-client` |
| ryll | `ryll-wt-vdi-tokens` | `vdi-console-tokens` |

The four doc PRs are **independent** — no cross-repo SHA pinning, no ordering
constraint. This plan file lives in kerbside alongside the phase 5–7 plans,
following the established home for this feature's detailed phase plans.

## What is already documented (do not redo)

Reconnaissance across all four repos found that phases 3 and 4 **already
shipped much of the user-facing documentation**, and phase 6 finished the
kerbside schema docs. Phase 8's real weight is therefore **Shaken Fist's
native docs**, which are the one place the feature is 100% absent. Already
done:

- **client-python:** commit `83933b7` (phase 4) added a full "VDI console
  access" section to `README.md` (lines 9–45) and good Click help strings
  for `instance vdiconsole` (`shakenfist_client/commandline/instance.py`
  lines 665–677), plus the `[vdi]` extra in `pyproject.toml` (lines 51–53),
  already mentioned in the README install section.
- **ryll:** commit `33267cb` (phase 3) added the pip-install subsection to
  `README.md` (`### Installing via pip`, lines 55–87) and to
  `docs/installation.md` (lines ~86–91), next to build-from-source.
- **kerbside:** `docs/schema.md` already documents `sf_token_jtis`
  (lines 139–148) and `sf_token_keys` (lines 150–160) and their
  relationships — no schema-doc work remains.

So client-python and ryll phase-8 work is **verify + propagate + fill the
one real gap each**, not net-new authoring. Shaken Fist and kerbside carry
the substantive writing.

## Cross-cutting facts the writing must respect

1. **Two different `KERBSIDE_`-namespaces exist and must never be
   conflated.** Shaken Fist's `KERBSIDE_URL` and `KERBSIDE_TOKEN_DURATION`
   are *Shaken Fist* cluster config (SF's `config.py`, `SHAKENFIST_`-prefixed
   / `cluster_config`). The kerbside **proxy daemon** has its own
   `KERBSIDE_`-prefixed env vars (`kerbside/config.py`, documented in
   `docs/components/kerbside/configuration.md`), e.g.
   `KERBSIDE_SF_CONSOLE_TOKEN_AUDIENCE`. Every doc that names a
   `KERBSIDE_*` knob must say which side owns it.
2. **The kerbside repo is the single source of truth for its own docs.**
   Shaken Fist's `docs/components/kerbside/` is an **automated sync** of the
   kerbside repo's `docs/` (SF commits "Automated documentation sync for
   …"). Author every kerbside doc change in the **kerbside repo only**; the
   SF mirror updates on the next automated sync. Never hand-edit
   `shakenfist/docs/components/kerbside/`.
3. **Manual enablement only, today.** The master plan (lines 179–182,
   675–684) defers ansible-collection kerbside deployment to future work, so
   operator docs must describe the **manual** enablement path (`sf-ctl` /
   `extra_config`), not an automated one.
4. **Audience == exchange-URL base.** SF's `KERBSIDE_URL` is simultaneously
   the exchange-URL base and the token `aud`; kerbside's expected audience
   (`SF_CONSOLE_TOKEN_AUDIENCE`, else derived from `PUBLIC_FQDN`) must equal
   it exactly. State this on both sides.
5. **Never print token or private-key material** in any example or command
   output shown in docs.
6. **House styles differ per repo** — match each: SF is MkDocs Material
   (sentence-case headings, `???`/`???+` admonitions, OpenAPI links, nav in
   `mkdocs.yml`, version markers like "Since v0.8"); kerbside/ryll are plain
   Markdown with a trailing `## Related Documentation` link list, ~79–80 col
   wrap; client-python is README + `--help`.

## Decisions

1. **SF gets a dedicated operator page for the integration:**
   `docs/operator_guide/vdi_console_tokens.md` (net-new), covering enable
   (`KERBSIDE_URL`, `KERBSIDE_TOKEN_DURATION`), signing-key custody and the
   rotation runbook (`KERBSIDE_JWT_SIGNING_KEY` in `cluster_config`, `sf-ctl
   ensure-kerbside-signing-key`), `spice_server_cert_subject` → kerbside
   `host_subject`, the manual-enablement caveat, and the `KERBSIDE_*`
   naming disambiguation. Rationale: the runbook is too large for
   `operator_guide/authentication.md` and benefits from being linkable from
   both `consoles.md` and `installation.md`. Added to `mkdocs.yml` nav.
2. **SF user-facing console how-to extends `consoles.md`; no new user
   page.** A new subsection after line 96 (before `## Screen captures`).
3. **No net-new CLI reference doc in client-python.** `--help` + the README
   remain the CLI reference (the repo has no `docs/` reference today and no
   mkdocs/sphinx). Phase 8 there is propagation into `AGENTS.md` /
   `ARCHITECTURE.md` / `CLAUDE.md` plus one help-string polish.
4. **ryll phase 8 is the release runbook plus a verify pass.** The pip
   README already exists; the real gap is `docs/releasing.md` (no PyPI wheel
   lane). No net-new `ryll/README.md`; the root README suffices. The
   README "pip under `## Installation` vs source under `## Building`"
   adjacency nit is left as an optional low-value polish, not required.
5. **kerbside `console-sources.md` Shaken Fist section is rewritten to
   mirror the OpenStack section's shape** (prose flow → options table →
   note), the one structural template already in that file for a
   source with a token-exchange flow.
6. **Status/bookkeeping flips land with the work**, not before: SF
   `docs/features.md`, `docs/release_notes/v07-v08.md`, both plan indexes,
   and the master execution table.

## Execution

Effort/model per step. Prose that carries security semantics (the operator
key-custody/rotation runbook, the offline-verification trust model) is
high-effort and reviewed against the code; mechanical propagation and table
edits are low-effort. Author kerbside docs in the kerbside repo only
(decision cross-cutting fact 2).

### A. Shaken Fist (worktree `shakenfist-wt-vdi-tokens`, branch `vdi-console-tokens`)

The largest surface — the feature is absent from every SF native doc.

| Step | Effort | Model | Brief |
|------|--------|-------|-------|
| A1 | high | opus | **New `docs/operator_guide/vdi_console_tokens.md`.** The operator runbook: what the integration is (SF mints an Ed25519 JWT, kerbside verifies it offline and proxies SPICE); enabling it — set `KERBSIDE_URL` (via `extra_config` at install, or `sf-ctl set-cluster-config`) and optionally `KERBSIDE_TOKEN_DURATION` (default 300 s); signing-key custody — `KERBSIDE_JWT_SIGNING_KEY` lives in `cluster_config` (custody parallel to `AUTH_SECRET_SEED`), lazily auto-generated on first mint or pre-created with `sf-ctl ensure-kerbside-signing-key`; **rotation runbook** — publish current+previous public keys via `/admin/vditokenpubkey`, sign with current, drop the old after the max token TTL; per-node `spice_server_cert_subject` → kerbside `host_subject` enforcement; the `KERBSIDE_URL` (SF) vs kerbside-proxy `KERBSIDE_*` disambiguation; and the manual-enablement caveat. End with links to `consoles.md`, `installation.md`, and the kerbside component pages. Verify every claim against `util/vdi_tokens.py`, `client/ctl.py`, `config.py`. |
| A2 | medium | opus | **Extend `docs/user_guide/consoles.md`** (new subsection after line 96): how a user opens a *proxied* console vs direct-to-hypervisor — `sf-client instance vdiconsole` seamless flow (mint → viewer), the `[vdi]` extra / ryll and the viewer chain (link to client-python), requirements (`state==created`, videospec `vdi` starts with `spice`), single-use ~5 min token, and graceful fallback to the direct `.vv` when `KERBSIDE_URL` is unset. Link to the A1 operator page. |
| A3 | medium | sonnet | **API reference.** In `docs/developer_guide/api_reference/instances.md` add `GET /instances/<ref>/vdiconsoleproxy` (returns `{url, expires_at}`, url = `<KERBSIDE_URL>/sf-console.vv?token=<jwt>`) alongside the existing `vdiconsolehelper` (lines 559–570), matching the admonition/OpenAPI-link style. In `docs/developer_guide/api_reference/admin.md` add a `## VDI token public keys` section for `GET /admin/vditokenpubkey` (append after line 30; also add the adjacent-and-currently-undocumented `GET /admin/cacert` it mirrors, if cheap). |
| A4 | low | sonnet | **`ARCHITECTURE.md` + `AGENTS.md` touch-ups** (master plan line 533). ARCHITECTURE.md: add the offline-signing / key-custody trust model to `## Security Model` (~line 895), and mention the endpoints in `### REST API surface` (~line 597) / config in `## Configuration` (~line 791). AGENTS.md: a short note that the mint path exists (`external_api/instance.py` `InstanceVDIProxyConsoleHelperEndpoint`, `util/vdi_tokens.py`, `external_api/admin.py` `AdminVDITokenPublicKeyEndpoint`) and where the signing key lives. |
| A5 | low | sonnet | **Bookkeeping.** Add the new operator page to `mkdocs.yml` nav (Operator Guide group, lines 298–312). Add a VDI-console-tokens bullet to `docs/release_notes/v07-v08.md` REST API section (after line 28). Add/adjust the `docs/features.md` matrix (VDI/console row, ~line 129). Optionally note `spice_server_cert_subject` in `docs/developer_guide/api_reference/nodes.md`. |

### B. kerbside (worktree `kerbside-wt-vdi-tokens`, branch `sf-vdi-tokens`)

Author here; SF's mirror auto-syncs.

| Step | Effort | Model | Brief |
|------|--------|-------|-------|
| B1 | high | opus | **Rewrite the `## Shaken Fist` section of `docs/console-sources.md`** (lines 33–49) to mirror the OpenStack section (lines 69–101): lead with prose narrating (a) cluster-wide scrape under a **system** credential, (b) the `GET /sf-console.vv?token=<jwt>` token exchange with **offline** Ed25519 verification (aud/exp/single-use jti, no cloud callback on the hot path), (c) `host_subject` pinned from the SF node's published SPICE cert subject; then an options table that adds `synthesize_host_subject` (per-source YAML knob, off by default; explain it only matches a stock cn=hostname PKI); then a Note. Also fix the "How Console Sources Work" bucket (lines 18–20) which currently mischaracterises SF as a plain once-a-minute scrape, and update the `sfmel` example (lines 192–200) if it needs the new knob shown. |
| B2 | low | sonnet | **`docs/configuration.md`: document `SF_CONSOLE_TOKEN_AUDIENCE`** (currently absent — violates the file's own "docs must match `config.py`" rule at lines 8–9). Add to Basic Settings or a short "Shaken Fist console tokens" subsection; reference `PUBLIC_FQDN` (line 49) and state it must equal SF's `KERBSIDE_URL`. |
| B3 | medium | sonnet | **`docs/proxy-architecture.md`: distinguish the two token surfaces** in Security Considerations / Token Validation (lines 130–151): the HTTP JWT bearer exchanged at `/sf-console.vv` (offline Ed25519 verify, jti replay table) vs the on-wire SPICE ticket the proxy actually authorizes via `AuthorizeConnection` — and that the proxy never sees the JWT. Optionally note in TLS Configuration (lines 54–63) that for SF consoles the enforced `host_subject` originates from the SF node cert subject at scrape time. |
| B4 | low | sonnet | **`ARCHITECTURE.md` enrich** (kerbside repo): add `/sf-console.vv` to the endpoints table (after line 218), add `sf_token_jtis`/`sf_token_keys` to the tables list (after line 254), enrich the Shaken Fist source row (line 264), and add the offline-JWT/jti-replay model to the Security Model (~lines 350–356). Also update `docs/index.md` Console Sources one-liner (lines 137–138) to include static + the token exchange. |

### C. client-python (worktree `client-python-wt-vdi-tokens`, branch `vdi-console-tokens-client`)

README + CLI help already landed in phase 4; this is propagation + one polish.

| Step | Effort | Model | Brief |
|------|--------|-------|-------|
| C1 | low | sonnet | **Propagate the feature into the meta-docs.** `AGENTS.md`: note `commandline/instance.py` hosts the `vdiconsole` proxy/direct launch and the new `apiclient` methods (`get_vdi_console_proxy`, `get_vdi_console_proxy_file`, `get_vdi_token_public_keys`), and mention `pip install -e ".[vdi]"`. `ARCHITECTURE.md`: add the `vdi` extra/ryll to Build and Packaging (lines 85–90) and the VDI methods to the Client class description (lines 44–56). `CLAUDE.md`: a "Recent changes" entry for the seamless VDI launch. |
| C2 | low | sonnet | **Polish `vdiconsolefile`'s command help** (`shakenfist_client/commandline/instance.py:746`) to mention `--direct`/proxy behaviour, matching `vdiconsole`'s improved help. Optionally tighten the README note that the direct path needs the `vdi-console-helper` capability. |

### D. ryll (worktree `ryll-wt-vdi-tokens`, branch `vdi-console-tokens`)

pip README already landed in phase 3; the gap is the release runbook.

| Step | Effort | Model | Brief |
|------|--------|-------|-------|
| D1 | medium | sonnet | **`docs/releasing.md`: add the PyPI wheel lane.** Add a PyPI row to the Artifacts table (after line 166: `ryll-<version>-py3-none-manylinux_2_28_{x86_64,aarch64}.whl`). Extend Prerequisites (~line 53) with a "PyPI Trusted Publishers / `release` environment" subsection (the one-time TestPyPI+PyPI trusted-publisher and manual-approval-environment setup), and the release-process narrative (~lines 151–157) with the new `build-ryll-wheels` → `sign-tag` → `publish-ryll-testpypi` → `publish-ryll-pypi` jobs and Sigstore tag signing. Source of truth: `.github/workflows/release.yml`, `tools/build-ryll-wheel*.sh`. |
| D2 | low | sonnet | **Verify pass.** Confirm the phase-3 pip subsections in `README.md` (55–87) and `docs/installation.md` are still accurate (package name `ryll`, Linux x86_64+aarch64 glibc ≥ 2.28, runtime apt libs). Optionally add a `docs/index.md` link to installation.md. Do **not** author a new `ryll/README.md`. |

### E. Status and index flips (do last)

| Step | Effort | Model | Brief |
|------|--------|-------|-------|
| E1 | low | sonnet | kerbside: flip phase 8 in `docs/plans/index.md` (line 19 row) and `docs/plans/PLAN-kerbside-vdi-tokens.md` (line 35). SF: flip phase 8 in `docs/plans/index.md` (line 145 area) and the master `docs/plans/PLAN-kerbside-vdi-tokens.md` execution table + phase-8 detail. |

## Success criteria

* A user can find, in the Shaken Fist docs, how to open a proxied console
  (`sf-client instance vdiconsole`, the `[vdi]` extra, requirements,
  fallback) — `docs/user_guide/consoles.md`.
* An operator can enable and operate the integration from the docs alone:
  set `KERBSIDE_URL`, bootstrap and **rotate** the signing key, and
  understand `host_subject` provenance — `docs/operator_guide/
  vdi_console_tokens.md`, cross-linked from installation and consoles.
* The new REST endpoints (`/instances/<ref>/vdiconsoleproxy`,
  `/admin/vditokenpubkey`) appear in the API reference.
* kerbside's `console-sources.md` Shaken Fist section documents the token
  exchange, offline verification, cluster-wide scrape, `host_subject`, and
  `synthesize_host_subject`; `SF_CONSOLE_TOKEN_AUDIENCE` is in
  `configuration.md`.
* client-python meta-docs and ryll's release runbook reflect the shipped
  feature; no doc conflates SF's `KERBSIDE_URL` with the kerbside-proxy
  `KERBSIDE_*` env.
* No token or private-key material appears in any example. Each repo's docs
  build/lint stays green (SF `mkdocs build`; others as applicable).

## Dependencies and out of scope

* **Independent of merge order** — all four doc PRs stand alone.
* **Out of scope → future work:** ansible-collection automation of kerbside
  deployment (master plan lines 675–684); operator docs describe the manual
  path only.
* **Out of scope:** pre-existing unrelated staleness surfaced during recon —
  kerbside `README.md`'s old flat `docs/…` (non-`spice/`) links, and the
  README pip/source heading-adjacency nit. Note them; do not fix under this
  feature.
* **Phase 9** (real cross-repo e2e + the kerbside exchange lane) is separate
  and post-merge.
