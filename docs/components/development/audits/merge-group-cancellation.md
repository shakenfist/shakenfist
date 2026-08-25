# Audit: Merge group run cancellation

## What we check

Expensive jobs that can run on a `merge_group` event must sit in a
concurrency group that a superseding merge group joins -- so the
older run is cancelled rather than left to run a full cloud build
nobody is waiting on -- and that nothing running *beside* them
joins, so the cancellation takes only what is superseded.

Concretely, for every workflow with a `merge_group:` trigger and every
reusable `workflow_call:` workflow, each job that holds a scarce
self-hosted runner and is reachable on `merge_group` must have an
effective `concurrency:` block -- job-level, or workflow-level if the
job declares none -- that:

* sets `cancel-in-progress: true`;
* keys the group on something stable across queue rebuilds. Either the
  group expression does not mention a per-rebuild context at all, or
  it branches on `github.event_name == 'merge_group'` and uses a
  stable key on that branch. The per-rebuild contexts are
  `github.ref` (and `ref_name` / `ref_type`), `github.sha`,
  `github.run_id`, `github.run_number`, `github.run_attempt` and the
  `github.event.merge_group` head contexts. `github.sha` is on that
  list because on `merge_group` it is the per-attempt merge commit,
  not the pull request head -- it is the key somebody reaches for on
  noticing that `github.ref` is wrong, and it is just as unique per
  rebuild;
* varies between the lanes of its own matrix. A matrix job's group
  must carry a `matrix.` context.

And on the two sides of a `uses:`:

* a reusable workflow that declares `workflow_call` inputs must key
  its group on at least one of them. A group made only of caller
  contexts renders identically for every invocation on a ref, so the
  second invocation cancels the first;
* a job that invokes a reusable workflow more than once per ref --
  because it is a matrix job, or because a sibling job invokes the
  same callee -- must pass a `concurrency_key` input, distinct per
  invocation and varying per matrix lane. That is the fleet
  convention shakenfist/actions' `smoke-cluster.yml` declares the
  input for; a callee cannot see its caller's job name, so the caller
  has to say which invocation it is. A callee invoked once per ref
  needs nothing: there is nothing for it to be distinct from.

Two further conditions are checked once per repository rather than
per job. The default branch's merge queue must build one entry at a
time wherever the `base_ref` key is used (see [Why cancelling is safe
here](#why-cancelling-is-safe-here)), and a `uses:` job may only
delegate to a callee this audit can see: in-repo, or another
`shakenfist/` repository, both of which are audited in their own
right. Anything else is a concurrency group nobody has checked and
the caller cannot fix.

"Scarce" means any self-hosted pool except `static`. The sibling
`expensive-lane-path-filter` audit uses the narrower `vm` label, which
is right for the question it asks, but instar's ephemeral runners are
tagged `[self-hosted, debian-12, xl]` with no `vm` label and an
abandoned merge group holds one of those just as firmly. The `static`
pool is exempt because it is always-on and shared, and the jobs on it
(path filters, gate jobs) are seconds long. GitHub-hosted runners are
exempt: there is no fleet runner to starve. A `runs-on:` expression is
resolved against the matrix it reads, so `runs-on: ${{ matrix.runner }}`
over self-hosted label lists is in scope; ryll's genuinely
GitHub-hosted Windows and macOS matrix is not.

Reusable workflows are in scope unconditionally, because a callee
published for the fleet cannot know what event it will see. Inferring
reachability from in-repo callers was tried and is wrong: it exempted
shakenfist/actions' `smoke-cluster.yml` -- which every shakenfist merge
group runs four nested clusters through -- on the strength of a
scheduled canary also calling it.

Out of scope: jobs whose `if:` excludes `merge_group`. Deliberate
exceptions are marked with an `audit-ok: merge-group-cancellation`
comment, read per job: a marker inside a job exempts that job, and
only a marker above the `jobs:` key exempts a whole file. A workflow
here runs to eight hundred lines and fifteen jobs, and one job's
stated exception should not quietly stop the other fourteen being
measured. The fleet has one exception: `test-drift-fix.yml` is
reusable, but its only caller is `pr-fix-tests.yml` on
`issue_comment`, so it can never see a merge group.

## Why

`github.ref` is the natural concurrency key and is correct on every
event except this one. On `merge_group` it is the per-attempt queue
branch, `gh-readonly-queue/<base>/pr-<N>-<SHA>`, and GitHub mints a
fresh SHA every time it rebuilds the group -- which it does on every
push to the base branch. Keying on it therefore puts every rebuild in
a concurrency group of its own, `cancel-in-progress` never matches,
and superseded runs are never cancelled. They run to completion
against a queue branch GitHub has already abandoned.

The cost is not theoretical. In shakenfist/kerbside#284, three merge
groups for the same pull request built three complete oVirt clouds
concurrently on the shared sfcbr under-cloud; only the newest could
possibly merge. The lane that failed did so by timing out waiting for
a 12 vCPU / 16GB instance the under-cloud could not place. The same
issue later recorded the cross-repository form: kerbside's merge group
starved on capacity consumed by two superseded
shakenfist/shakenfist merge groups. A fix in one repository does not
hold when its neighbours share the under-cloud, which is what makes
this a fleet audit rather than a per-project judgement call.

## Why cancelling is safe here

Cancelling a `merge_group` run that the queue is still waiting on
reports a failed required check and ejects the pull request. That is
avoided by the fleet's serial queue: the `merge-queue-config` audit
requires `max_entries_to_build: 1` on every repository with a merge
queue, so the queue only ever builds one entry at a time and any
*other* in-flight `merge_group` run is by definition superseded.

This audit therefore depends on [merge-queue-config.md](/components/development/audits/merge-queue-config/),
and checks the dependency rather than stating it: where a repository
uses the `base_ref` key below, the check asks GitHub whether that
repository's queue is serial and fails if it is not. If
`max_entries_to_build` were ever raised above 1, several merge groups
would be live at once, a base-branch key would alias them, and the
pattern below would start cancelling entries that are still wanted --
the key would have to narrow to something unique per live entry.

The same reasoning is what puts lane distinctness in this audit. An
ejected pull request is what the fleet is protected from at the queue
level, and it is exactly what a self-cancelling matrix produces at
the job level.

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
`refs/heads/develop` would share a group with a `workflow_dispatch`
run on `develop`, whose `github.ref` is the same string, and the two
would cancel each other.

In a reusable workflow, `github.workflow` resolves to the *caller's*
name, so keep whatever literal prefix the callee already uses to
separate components and substitute only the `github.ref` tail -- and
add the caller-supplied key, so two invocations on one ref are two
groups:

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

A matrix job substitutes its own lane into the group, and a matrix
job calling a reusable workflow does the same thing through the
input:

```yaml
      concurrency_key: ${{ matrix.merge.job_name }}
```

## Projects

<!-- consistency-audit:begin -->
*This table is regenerated daily by the consistency audit
workflow from `scripts/audit-check.py` results; do not edit
it by hand.*

Last regenerated: 2026-08-25T06:54:21.186929+00:00

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
