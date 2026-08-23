# Audit: Merge queue reasonability

## What we check

Repositories that enable a GitHub merge queue on their default
branch must configure it so entries are processed serially and
merged individually:

* `max_entries_to_build: 1` — no speculative stacking.
* `min_entries_to_merge: 1` — merge each green entry immediately.

Repositories without a merge queue are not in scope for this audit;
whether to adopt two-stage CI at all is a per-project decision.

The check reads the effective rules for the default branch via
`GET /repos/shakenfist/<repo>/rules/branches/<branch>` and validates
the parameters of any `merge_queue` rule found.

## Why these values

Two merge queue mechanics are easy to get wrong, and both were
learned the hard way on shakenfist/shakenfist (August 2026):

**Speculative stacking multiplies failures.** With
`max_entries_to_build` above 1, entry N+1 builds on a temporary
branch containing entry N's changes. When any entry ahead fails,
the entries behind it are ejected and rebuilt on new SHAs. On a CI
cluster whose dominant failure mode is load, this is doubly wrong:
the stacked builds waste runs (we observed single PRs rebuilt five
times in one day) and the extra concurrent merge groups add the
very load that causes the failures. A serialized queue
(`max_entries_to_build: 1`) never starts CI for an entry until the
entry ahead has merged or ejected, so a failure can never
invalidate work behind it.

**Batched merging is pure latency.** The queue always builds one
merge group and runs CI once per entry, no matter how merges are
batched — `min_entries_to_merge` only controls how many green
entries land in a single default-branch update. Raising it makes
the queue idle for up to `min_entries_to_merge_wait_minutes`
hoping more PRs arrive, which on a mostly-single-developer project
delays every merge and saves nothing. With
`min_entries_to_merge: 1` that wait timer never engages (it is the
timeout for reaching the minimum group size, which a single entry
already satisfies).

Other parameters (`grouping_strategy`, `merge_method`,
`max_entries_to_merge`, `check_response_timeout_minutes`) are
conventions rather than correctness issues and are not enforced;
the fleet reference is shakenfist/shakenfist's "Develop branch"
ruleset: ALLGREEN, MERGE, 5, and 360 respectively.

## Template

No template — this is a one-time ruleset configuration change.

To inspect the current configuration:

```bash
gh api repos/shakenfist/<repo>/rules/branches/develop \
    --jq '.[] | select(.type == "merge_queue") | .parameters'
```

To fix a non-compliant ruleset, fetch it, rewrite the two
parameters, and PUT it back (preserving everything else, including
`bypass_actors`):

```bash
gh api repos/shakenfist/<repo>/rulesets --jq \
    '.[] | select(.target == "branch") | {id, name}'
gh api repos/shakenfist/<repo>/rulesets/<id> | jq \
    '{name, target, enforcement, conditions,
      bypass_actors: (.bypass_actors // []),
      rules: (.rules | map(
        if .type == "merge_queue"
        then .parameters.max_entries_to_build = 1
           | .parameters.min_entries_to_merge = 1
        else . end))}' > /tmp/ruleset.json
gh api -X PUT repos/shakenfist/<repo>/rulesets/<id> \
    --input /tmp/ruleset.json
```

Afterwards, trigger the repository's `export-repo-config` workflow
so the change is captured in `.github/exported-config/`.

## Projects

<!-- consistency-audit:begin -->
*This table is regenerated daily by the consistency audit
workflow from `scripts/audit-check.py` results; do not edit
it by hand.*

Last regenerated: 2026-08-23T06:45:38.740880+00:00

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| client-python | N/A | - |
| client-python-k3s | compliant | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | N/A | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | N/A | - |
| shakenfist | compliant | - |
<!-- consistency-audit:end -->
