# Shaken Fist VDI console tokens (kerbside side)

This is the kerbside-side entry point for a **cross-repository
master plan** whose plan of record lives in the shakenfist
repository:

> `shakenfist/docs/plans/PLAN-kerbside-vdi-tokens.md`
> (branch `vdi-console-tokens` until merged)

## Summary

Give Shaken Fist per-instance console authorisation on a par
with (and better than) the OpenStack integration. Shaken Fist
mints a short-lived Ed25519-signed JWT for an instance the
caller owns and returns a URL pointing at kerbside. Kerbside
validates the token **offline** — signature against a public
key fetched from the cluster at source-init time, `aud`/`exp`
checks, single-use `jti` — then issues its usual 48-char
consoletoken and `.vv`, exactly as the `NovaToken` flow does,
but with no callback to the cloud on the exchange path.
Backend connection details always come from kerbside's scraped
console table, never from token claims.

## Kerbside-side phases

The master plan's execution table assigns these phases to this
repository; their detailed phase plans will be written here
when each phase starts:

| Phase | Plan | Status |
|-------|------|--------|
| 5. Exchange endpoint (`/sf-console.vv`, jti replay table, reaper) | [PLAN-kerbside-vdi-tokens-phase-05-exchange.md](/components/kerbside/plans/PLAN-kerbside-vdi-tokens-phase-05-exchange/) | Done |
| 6. Cluster-wide scrape + host_subject for SF consoles | [PLAN-kerbside-vdi-tokens-phase-06-scrape.md](/components/kerbside/plans/PLAN-kerbside-vdi-tokens-phase-06-scrape/) | Done |
| 7. Functional test: Shaken Fist mint path (SF-side only; kerbside exchange lane moved to phase 9) | [PLAN-kerbside-vdi-tokens-phase-07-ci.md](/components/kerbside/plans/PLAN-kerbside-vdi-tokens-phase-07-ci/) | Done |
| 8. Documentation (shared phase; here: console-sources.md rewrite) | [PLAN-kerbside-vdi-tokens-phase-08-docs.md](/components/kerbside/plans/PLAN-kerbside-vdi-tokens-phase-08-docs/) | Done |
| 9. Full cross-repo end-to-end lane + kerbside exchange/proxy lane + KERBSIDE_URL provisioning (real SF, post-merge) | [PLAN-kerbside-vdi-tokens-phase-09-e2e.md](/components/kerbside/plans/PLAN-kerbside-vdi-tokens-phase-09-e2e/) | Done |

Phases 0-4 (token format decisions, cluster signing key,
vdiconsoleproxy endpoint, pip-installable ryll, and
client/CLI/viewer-launch support) live in the shakenfist,
ryll, and client-python repositories — see the master plan
for the full table, the token-flow diagram, the alternatives
considered, and the open questions and their
recommendations.

## Post-completion defects (Shaken Fist side)

All kerbside-side phases are done and the exchange path works.
Two defects found on 2026-09-01, by running `sf-client instance
vdiconsolefile` against a Shaken Fist cluster with **no kerbside
deployed**, mean the master plan is not finished. Both are
shakenfist-only; neither needs a change here, and neither
affects a cluster that does run kerbside. They are recorded so
this side of the plan does not read as complete when the plan
as a whole is not:

- **shakenfist#4003** — Shaken Fist advertises the
  `vdi-console-proxy` capability unconditionally, even with
  `KERBSIDE_URL` empty, so `shakenfist_client` takes the proxy
  path on a kerbside-less cluster and fails instead of falling
  back to its direct-to-hypervisor `.vv`.

- **shakenfist#4009** — Shaken Fist's *direct* `.vv` (the
  non-kerbside path, `/vdiconsolehelper`) is malformed: `host=`
  carries a node UUID, `type=` carries an SF-internal enum
  value that virt-viewer rejects, and no `host-subject` is
  emitted. The last of those is worth noting here: the
  host_subject work in phase 6 and `PLAN-host-subject.md` gave
  kerbside-sourced consoles subject pinning, and the direct
  path never picked it up, so it is the weaker of the two
  paths on exactly the property that work set out to fix.

- **shakenfist#4004** — the Shaken Fist ansible collection has
  no `KERBSIDE_URL` knob, so no collection-deployed cluster can
  turn the integration on at all. This is the deployer phase
  the master plan deliberately deferred, now tracked as an
  issue.

The master plan carries these as its phase 11.

## Key kerbside touch points

- `kerbside/api.py:516` — the `NovaToken` resource the new
  `SfToken` exchange endpoint mirrors (route `/sf-console.vv`
  beside `/nova-console.vv`).
- `kerbside/consoletoken.py` — unchanged; the JWT is a bearer
  capability for one HTTP exchange and never enters the SPICE
  password field.
- `kerbside/sources/shakenfist.py` — gains public-key fetch at
  init (beside `get_cluster_cacert()`), cluster-wide scraping
  under a system credential, and `host_subject` population.
- `kerbside/main.py:185` — `_reap_expired_console_tokens` is
  the pattern for the new jti replay-table reaper.
- Alembic migration for the `sf_token_jtis` replay table.
