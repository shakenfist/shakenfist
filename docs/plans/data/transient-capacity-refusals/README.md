# Evidence for "A capacity refusal is transient"

This directory holds the analysis script behind the Situation section of
[PLAN-transient-capacity-refusals.md](../../PLAN-transient-capacity-refusals.md).
The raw inputs are CI job bundles, which are too large to commit and expire
from GitHub's artifact retention after 90 days; the recipe below reproduces
the tables from any run's bundle while it exists.

## Inputs

For each merge run read on 2026-09-08 the input was the failing smoke job's
bundle artifact, which contains:

- `bundle/traces/headroom.jsonl` and `headroom-start` -- the 15 s capacity
  probe series from ci-cloud-sizing phase 1, and the test step's start time;
- `bundle/loki/` -- the Loki refusal census;
- `bundle/<node>/_commands/journalctl-sf-units` -- every node's
  `journalctl -u 'sf-*'` export, which is where the `instance placed`,
  `instance placement released`, `schedule has no candidates at stage ...`
  and reconcile events come from.

The runs, all `Functional tests` on `merge_group`:

| run | topology | note |
|---|---|---|
| 34163288637 | slim-tier | post-#4106 |
| 34171977552 | slim-tier | post-#4106 |
| 34178278720 | slim-tier | post-#4106 |
| 34119030297 | slim-tier | post-#4106; the measurement-bound refusal |
| 34125365386 | slim-tier | post-#4106 |
| 34168326220 | slim-primary | post-#4106 |
| 33991296717 | slim-tier | pre-#4106; four unforced refusals at 12/12 |
| 33948911843 | slim-tier | pre-#4106; #4087 residue (`0 / 6 / 3`) |

The 50 `slim-tier` records in
[`../ci-cloud-sizing-baseline/`](../ci-cloud-sizing-baseline/README.md) were
read for the saturation and abort-versus-failure figures.

## Recipe

```
run=34163288637
job=$(gh api "repos/shakenfist/shakenfist/actions/runs/$run/jobs?per_page=100" \
    --jq '.jobs[] | select(.conclusion=="failure") | select(.name|test("Smoke tests")) | .id')
gh api "repos/shakenfist/shakenfist/actions/runs/$run/artifacts" --jq '.artifacts[].name'
mkdir -p bundle_$run && gh run download "$run" -n <bundle artifact name> -D bundle_$run
python3 analyse_bundle.py bundle_$run
```

`analyse_bundle.py` expects `bundle_<run>/bundle/...` as laid out above and
prints, per run: the test step's start time, when the first capacity row
appeared, every `sufficient_idle_cpu` abort with the node it was forced onto
and that node's `measured / committed / limit` from the scheduler's own
`dropped` payload, the placed/released ledger reconstructed from the journals
at each abort, the reconciler's passes with their `drift_cpus`, the demand
guard refusals and waivers, and per-node saturation stretches. Its output for
the eight runs above is summarised in the plan; the script is committed so the
summary can be recomputed rather than trusted.

It is standard library only and touches no shakenfist code, so it runs on a
runner or a laptop with nothing installed.
