# Phase 4: Docs, plan index, and cleanup

Master plan: [PLAN-per-host-resource-reservations.md](PLAN-per-host-resource-reservations.md)

## Goal

Document the new reservation model and update the repository's plan bookkeeping.
Optionally build the `sf-ctl unset-config` primitive to retire the inert
`RAM_SYSTEM_RESERVATION` row (otherwise leave it and record the follow-up).

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | sonnet | none | Update operator docs for the config-key rename and new semantics: the three `NODE_*` reservation keys (per-node, set via `/etc/sf/config`, never `set-config`), CPU now in **threads**, disk reservation applied to all tracked filesystems and replacing `MINIMUM_FREE_DISK`. Update `docs/operator_guide/` where reservations / `MINIMUM_FREE_DISK` are described. If schema/db behaviour is unaffected, `docs/operator_guide/database.md` needs no change. |
| 4b | low | haiku | none | Update `ARCHITECTURE.md` / `README.md` / `AGENTS.md` only if they describe the reservation/config-loading behaviour materially changed here (e.g. an infra-role reservation mention). Otherwise skip and note why. |
| 4c | low | sonnet | none | In `docs/plans/index.md` add the Plan Status rows for this master plan (one row per phase, linked), and in `docs/plans/order.yml` add the master-plan entry `PLAN-per-host-resource-reservations.md` in a sensible position (near `PLAN-scheduler-reservations.md`, since both touch scheduling). Do not list phase files in `order.yml`. |
| 4d (optional) | high | opus | none | Build `sf-ctl unset-config <KEY>`: a delete path in `mariadb.py` (direct + gRPC, mirroring `set_cluster_config`; may need a proto method → regenerate with `tox -e genprotos`), the CLI command in `client/ctl.py`, and a deploy step (or one-off note) to remove the stale `RAM_SYSTEM_RESERVATION` row. Only do this if the operator wants the dead key gone; otherwise leave inert and keep this as Future work. |

## Verification

- Docs build/lint if the repo has a docs check; links resolve.
- `docs/plans/index.md` status column reflects reality as phases complete.
- If 4d is done: `sf-ctl show-config` no longer lists `RAM_SYSTEM_RESERVATION`
  after the cleanup runs, and `tox -e genprotos` output (if any) is committed.
