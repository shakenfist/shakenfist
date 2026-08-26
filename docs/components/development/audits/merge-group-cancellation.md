# Audit: Merge group run cancellation

## What we check

Expensive jobs reachable on a `merge_group` event must sit in a
concurrency group that a superseding merge group joins, so the older
run is cancelled rather than left building a cloud nobody is waiting
on -- and that nothing running *beside* them joins, so the
cancellation takes only what is superseded.

For every workflow with a `merge_group:` trigger, and every reusable
`workflow_call:` workflow, each job holding a scarce self-hosted
runner needs an effective `concurrency:` block -- job-level, or
workflow-level if the job declares none -- that:

* sets `cancel-in-progress: true`;
* keys on something stable across queue rebuilds. Either the group
  expression mentions no per-rebuild context, or it branches on
  `github.event_name == 'merge_group'` and uses a stable key there.
  The per-rebuild contexts are `github.ref` (and `ref_name` /
  `ref_type`), `github.sha`, `github.run_id`, `github.run_number`,
  `github.run_attempt` and the `github.event.merge_group` head
  contexts. `github.sha` is on the list because on `merge_group` it is
  the per-attempt merge commit, not the pull request head -- it is
  what somebody reaches for on noticing `github.ref` is wrong, and it
  is just as unique per rebuild;
* varies between the lanes of its own matrix: a matrix job's group
  must carry a `matrix.` context.

Across a `uses:`:

* a reusable workflow declaring `workflow_call` inputs must key its
  group on at least one of them. A group made only of caller contexts
  renders identically for every invocation on a ref, so the second
  cancels the first;
* a caller invoking a reusable workflow more than once per ref --
  a matrix job, or a sibling job invoking the same callee -- must pass
  a `concurrency_key` input, distinct per invocation and varying per
  matrix lane. A callee cannot see its caller's job name, so the
  caller has to say which invocation it is. Invoked once per ref, it
  needs nothing.

Two conditions are checked once per repository: the default branch's
merge queue must be serial wherever the `base_ref` key is used (see
[Why cancelling is safe here](#why-cancelling-is-safe-here)), and a
`uses:` job may only delegate to a callee this audit can see --
in-repo, or another `shakenfist/` repository. Anything else is a
concurrency group nobody has checked and the caller cannot fix.

**Scarce** means any self-hosted pool except `static`. `static` is
exempt because it is always-on, shared, and its jobs are seconds long;
GitHub-hosted runners are exempt because there is no fleet runner to
starve. A `runs-on:` expression is resolved against the matrix it
reads, so `runs-on: ${{ matrix.runner }}` over self-hosted label lists
is in scope. (The sibling `expensive-lane-path-filter` audit uses the
narrower `vm` label, which is right for its question; instar's
ephemeral runners are `[self-hosted, debian-12, xl]` with no `vm`
label and an abandoned merge group holds one just as firmly.)

Reusable workflows are in scope unconditionally: a callee published
for the fleet cannot know what event it will see. Inferring
reachability from in-repo callers was tried and is wrong -- it exempted
`shakenfist/actions`' `smoke-cluster.yml`, which every shakenfist merge
group runs four nested clusters through, because a scheduled canary
also calls it.

Out of scope: jobs whose `if:` excludes `merge_group`. Deliberate
exceptions take an `audit-ok: merge-group-cancellation` comment, read
per job -- a marker inside a job exempts that job, and only a marker
above the `jobs:` key exempts the file, because one job's stated
exception should not quietly stop the other fourteen being measured.
The fleet has one: `test-drift-fix.yml` is reusable, but its only
caller is `pr-fix-tests.yml` on `issue_comment`.

## Why

`github.ref` is the natural concurrency key and is correct on every
event except this one. On `merge_group` it is the per-attempt queue
branch, `gh-readonly-queue/<base>/pr-<N>-<SHA>`, and GitHub mints a
fresh SHA on every rebuild -- which it does on every push to the base
branch. Keying on it puts each rebuild in a group of its own,
`cancel-in-progress` never matches, and superseded runs run to
completion against a queue branch GitHub has already abandoned.

In shakenfist/kerbside#284 three merge groups for one pull request
built three complete oVirt clouds concurrently on the shared sfcbr
under-cloud; only the newest could merge, and the lane that failed
timed out waiting for an instance the under-cloud could not place. The
same issue records the cross-repository form: kerbside's merge group
starving on capacity two superseded shakenfist/shakenfist merge groups
held. A fix in one repository does not hold when its neighbours share
the under-cloud, which is what makes this a fleet audit.

## Why cancelling is safe here

Cancelling a `merge_group` run the queue is still waiting on reports a
failed required check and ejects the pull request. The fleet's serial
queue is what avoids that: [merge-queue-config.md](/components/development/audits/merge-queue-config/)
requires `max_entries_to_build: 1`, so only one entry builds at a time
and any *other* in-flight `merge_group` run is by definition
superseded.

This audit checks that dependency rather than stating it: where a
repository uses the `base_ref` key, it asks GitHub whether the queue is
serial and fails if it is not. Raise `max_entries_to_build` above 1 and
a base-branch key would alias several live entries, cancelling ones
still wanted -- the key would have to narrow to something unique per
entry.

The same reasoning puts lane distinctness here. An ejected pull request
is what the serial queue protects against, and it is exactly what a
self-cancelling matrix produces at the job level.

## Template

No template -- the change is a concurrency key edit per workflow. The
fleet pattern, from kerbside's `functional-tests.yml`:

```yaml
    concurrency:
      group: >-
        ${{ github.workflow }}-<job suffix>-${{
        github.event_name == 'merge_group'
        && format('merge_group-{0}', github.event.merge_group.base_ref)
        || github.ref }}
      cancel-in-progress: true
```

The `merge_group-` prefix matters: without it a queue run keyed on
`refs/heads/develop` would share a group with a `workflow_dispatch` run
on `develop`, whose `github.ref` is the same string, and the two would
cancel each other.

In a reusable workflow `github.workflow` resolves to the *caller's*
name, so keep the literal prefix the callee already uses, substitute
only the `github.ref` tail, and add the caller-supplied key:

```yaml
    concurrency:
      group: >-
        smoke-cluster-${{ inputs.component }}-${{ inputs.tier }}-${{
        inputs.concurrency_key }}-${{
        github.event_name == 'merge_group'
        && format('merge_group-{0}', github.event.merge_group.base_ref)
        || github.ref }}
      cancel-in-progress: true
```

A matrix job substitutes its own lane into the group, and a matrix job
calling a reusable workflow does the same through the input:

```yaml
      concurrency_key: ${{ matrix.merge.job_name }}
```

## Projects

<!-- consistency-audit:begin -->
*Generated 2026-08-26T06:56:26.297909+00:00 from `scripts/audit-check.py`; do not edit.*

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
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
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | N/A | - |
| shakenfist | compliant | - |
<!-- consistency-audit:end -->
