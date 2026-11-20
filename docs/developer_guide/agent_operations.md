# Agent operations

An agent operation is a unit of work Shaken Fist asks the in-guest
`sf-agent2` process to do on your behalf: execute a command, place a
blob inside the instance, or fetch a file out of it. Each of the three
creating calls returns an `AgentOperation` object straight away, with a
UUID of its own, and the work happens later -- so the object, not the
HTTP response, is what a caller tracks.

This page is about the model a caller programs against: what order
operations run in, what one operation's failure means for the next one,
and how to say that one operation must not run until another has
finished. It is not the field-level contract. For the parameters each
call accepts, the refusals they can produce and the shape of every
object involved, see
[the instances API reference](api_reference/instances.md#executing-commands-within-an-instance)
and
[the agent operations API reference](api_reference/agentoperations.md).
For how a deployment is tuned, and what to change when operations are
being killed or aborted,
see [the operator guide](../operator_guide/agent_operations.md).

## One executor per instance

The sidechannel daemon runs at most one executor per instance. An
operation is dispatched only when that instance has no other operation
executing, which makes the queue head the unit of recovery: if the
daemon restarts mid-operation, the queue entry is still there to be
retried or retired. See
[one executor per instance](../operator_guide/agent_operations.md#one-executor-per-instance)
for the operational consequences.

Two things follow for a caller. Operations against one instance run
strictly in the order they were created, with no concurrency to reason
about and no way to ask for any. And an operation which wedges holds
that instance's entire agent surface, including operations somebody
else submitted, until a timing budget or the reaper ends it.

Operations against *different* instances have no relationship at all.
Each instance has its own queue and its own executor, so two operations
on two instances may run in either order, or at the same time, unless
you say otherwise.

## The timing budgets

Three budgets bound an agent operation, and a caller needs to know
which one a failure came from rather than what any of them are set to.
`deadline_seconds` is a wall-clock budget on the operation's whole
life, counted from when the API server received the request, so queue
time spent behind another operation is spent against it.
`progress_timeout_seconds` bounds how long a transfer may make no
forward progress, and the API refuses it on `agent/execute`, where
nothing is observable as progress. Both are per-request parameters with
server defaults, described in
[bounding how long an agent operation may take](api_reference/instances.md#bounding-how-long-an-agent-operation-may-take).
The third, `AGENT_OPERATION_DEPENDENCY_WAIT`, is not a per-request
parameter at all: it is the operator's bound on how long a
dependency-blocked operation may hold its instance's queue, and it has
a section of its own below.

The deployment's values for all three, and for the attempt cap and the
ceiling a caller may ask for, are in
[the two budgets](../operator_guide/agent_operations.md#the-two-budgets)
and
[what to tune, and when](../operator_guide/agent_operations.md#what-to-tune-and-when).

## Ordering is not dependency

By default the queue gives you ordering and nothing else. Each
operation runs whatever the one before it did: a shell `;`, not a
`&&`. If you submit a command which writes a file and then a command
which reads it, the second one runs on the schedule the first one
established, but it also runs when the first one errored, expired or
was aborted -- against whatever the guest looks like at that point.

There is one exception, and it is inside a single operation rather than
between two. An operation carries a list of commands, and that list
does abort at the first failure. It is not composable from the API,
though: you cannot put half of a chain in one operation's command list
and reach it from another operation.

What bridges the gap is a dependency edge, which is a statement about
two operations rather than about queue adjacency -- and that is what
makes it correct in a queue shared by callers who do not know about
each other.

## Declaring dependencies

All three creating calls take two optional lists of edges, each naming
agent operations which already exist:

* **`depends_on` is fate sharing.** The new operation runs only if the
  operation it names succeeded. If that operation errored, expired, was
  aborted or has gone away, the dependent does not run at all: it is
  moved to the `abort` terminal state, and its `dependency_failure`
  field says which dependency, and in what state, caused that.
* **`runs_after` is ordering only.** The new operation waits for the
  named operation to finish and then runs, whatever that operation's
  outcome was. A `runs_after` edge never aborts anything and never sets
  `dependency_failure`.

An edge may name an operation on a *different* instance, which is what
makes a chain able to span a fleet. Nothing about evaluation is
instance-aware: the dispatcher reads the named operation's state by
UUID.

An edge may also carry a settle delay, for the case where the guest
needs a moment after whatever the dependency changed. The delay runs
from when the dispatcher observed the dependency finish, not from when
it actually finished, so it is a floor rather than an exact wait.

Two properties of edges are worth knowing before you build on them, and
both are properties of the design rather than defects: a dependent
still reads as `queued` for some time after its dependency has already
failed, and a dependency which has been swept cannot be told from one
which failed. Both are written up in
[declaring dependencies between agent operations](api_reference/instances.md#declaring-dependencies-between-agent-operations),
along with the capability token a server advertises to say it
understands these parameters at all.

## The cascade to `abort`

`abort` is a terminal state an agent operation reaches only through a
`depends_on` edge. Nothing else writes it, so a caller which never
declared a dependency will never see one -- which is why it was safe to
introduce.

The cascade is evaluated when a dependent reaches the head of its
instance's queue, not when its dependency fails, and it is not
transitive in one step: the first dependent is aborted, the operation
which depended on *that* is aborted when it reaches the head in turn,
and so on down the chain. Each aborted operation records the
dependency which aborted it, so the chain can be walked backwards to
the operation which actually failed. `await_agent_chain()` does that
walk for you.

`abort` is distinct from the other two ways an operation can fail.
`expired` means one of its timing budgets ran out, and `error` means
the operation itself failed; see
[telling `expired` from `error`](../operator_guide/agent_operations.md#telling-expired-from-error).
A client awaiting a chain has to treat all three as terminal, because
a dependent's own deadline is enforced before its edges are looked at:
a dependent which sat too long reaches `expired` with no
`dependency_failure` at all.

## What waiting for a dependency costs

This is the one thing about dependencies which a reader is likely to
get wrong, because every other page describes the budget per
operation.

A blocked operation holds its instance's queue head, so the time it may
spend blocked is bounded by `AGENT_OPERATION_DEPENDENCY_WAIT`. **That
budget is anchored when the dispatcher first finds the operation
blocked, and not when the operation's dependency finishes.** Whether
that costs you anything depends on where the dependency lives, and the
difference is big enough to design around.

An edge to an operation on the **same instance** is free. The dependent
is queued behind its dependency; edges are evaluated only for the
operation at the head of a queue; and the dependent does not reach the
head until the dependency has gone terminal and been retired, by which
time the edge is already satisfied. Such an operation is never found
blocked, so no budget is ever started, however long the dependency
took.

An edge **across instances** is the expensive one. The dependent
reaches the head of its own queue immediately, is found blocked on the
first dispatcher pass, and spends its budget while the operations
upstream of it run. So the last link of a cross-instance chain
submitted all at once is bounded by one budget for the whole chain,
rather than one for its own hop.

What happens when the budget runs out is deliberately asymmetric. A
`depends_on` operation is aborted, with the dependency's state at that
moment in `dependency_failure` -- which may be `queued`, `executing` or
even `complete`, so the recorded state does not by itself mean the
dependency failed. A `runs_after` operation logs a warning and **runs
anyway**: an edge which only asked for ordering silently stops
ordering rather than failing.

The practical consequences:

* A chain on one instance needs no arithmetic. Put a file, run it,
  fetch what it produced is the canonical shape, and no step in it is
  racing a budget however slow it is.
* A chain which crosses instances should be short, and made of steps
  which are fast relative to the budget. Work which is slow by nature
  -- a package upgrade, an image pull -- belongs at the end of such a
  chain, or on the far side of an await.
* Serialising work across a whole fleet by chaining node to node is
  the worst case of that, and should not be attempted as a single
  submission: every link is cross-instance, so the last node's budget
  has to cover every node ahead of it. Submit a chain per node, await
  it, and move on; or chain only within each node and fan the nodes
  out, which is the last example below.

An operator who has deliberately raised the budget changes all of
that, and raising it has a cost of its own: a blocked operation holds
up everything behind it in its instance's queue, so a longer budget is
a longer stall for whoever else is using that instance. The trade-off,
and the ceiling the budget also places on a settle delay, are in
[what to tune, and when](../operator_guide/agent_operations.md#what-to-tune-and-when).

## Retry and attempts

An operation's `attempts` field counts how many times it has been
dispatched, and some failures are retryable: a stalled transfer can be
returned to the head of its queue for another attempt, while an
executed command never can be, because re-running a command list from
the start would repeat a side effect the agent cannot take back. The
rules, the attempt cap and the node-local reaper which resolves
operations the queue cannot tell are stuck are in
[retry and the executor reaper](../operator_guide/agent_operations.md#retry-and-the-executor-reaper).

Retry matters to a chain in one respect: a retried operation starts its
dependency wait budget again, because a retry is a fresh attempt at the
head of its instance's queue and the budget bounds one attempt's hold
on that head.

## Worked examples

These use `shakenfist_client`'s Python API client. All of them submit
with `await_seconds=0`, which returns the operation as the creating
call described it without polling: submitting a chain must not wait for
each link, since waiting for a link is the thing the edges exist to
avoid. An `ASYNC_CONTINUE` client does the same by default, and is what
a chain is ordinarily submitted with.

A server which does not understand the dependency parameters is
refused rather than quietly sent nothing, with an
`IncapableException`, because a dropped edge means "run now" -- which
is exactly what the caller declared the edge to prevent. Check
`check_capability('agentoperation-dependencies')` first if your code
has to work against both.

### Put a file, run it, fetch the result

Three links on one instance, each fate shared with the one before it.
Ordering here is free from the queue; what the edges buy is that the
script is not executed when the upload failed, and that nothing tries
to fetch a report which was never written.

```python
from shakenfist_client import apiclient

sf_client = apiclient.Client(async_strategy=apiclient.ASYNC_CONTINUE)

# blob_uuid names a blob which is already in the cluster. The API
# reference's instance upload example shows how a local file becomes
# one.
put = sf_client.instance_put_blob(
    instance_ref, blob_uuid, '/tmp/collect.sh', 0o755, await_seconds=0)

run = sf_client.instance_execute(
    instance_ref, '/tmp/collect.sh > /tmp/report.txt',
    depends_on=[apiclient.agent_dependency(put)],
    await_seconds=0)

fetch = sf_client.instance_get(
    instance_ref, '/tmp/report.txt',
    depends_on=[apiclient.agent_dependency(run)],
    await_seconds=0)

# Only the last link is awaited: every other one has to finish before
# it can run. If the chain broke, the exception names the operation
# which actually failed rather than the link which was awaited.
try:
    fetch = sf_client.await_agent_chain([put, run, fetch], timeout=120)
except apiclient.AgentOperationFailed as e:
    print(f'the chain failed at {e.op_uuid}: {e}')
    raise

report_blob = fetch.get('results', {}).get('0', {}).get('content_blob')
if report_blob:
    with open('/tmp/report.txt', 'wb') as f:
        for chunk in sf_client.get_blob_data(report_blob):
            f.write(chunk)
```

### Ordering across two instances

Here the ordering is the whole point, and the outcome is not: the
application's cache is flushed after the database has been reloaded,
whether or not the reload reported success, and after a settle delay
which gives the database a moment to come back.

```python
reload = sf_client.instance_execute(
    db_instance, 'systemctl reload postgresql', await_seconds=0)

flush = sf_client.instance_execute(
    app_instance, 'myapp-cli cache flush',
    runs_after=[apiclient.agent_dependency(reload, settle_seconds=10)],
    await_seconds=0)

sf_client.await_agent_chain([reload, flush], timeout=120)
```

Note that the two operations are on different instances. Two
operations on the *same* instance are already ordered by its queue, so
a `runs_after` edge between them buys nothing.

`await_agent_chain()` is a complete report only for a `depends_on`
chain, where a failure cascades forward to the link being awaited.
Here `flush` can complete while `reload` errored, and that completion
is what comes back, so a caller who cares has to read the earlier
link's state itself.

### Fanning out across a fleet

One short chain per node, submitted for every node and then awaited
one node at a time. Each node's `dist-upgrade` is fate shared with its
own `apt-get update`, so a node whose index refresh failed does not
upgrade against a stale one -- which is what the same two commands
submitted without edges would do. Every edge here names an operation on
the same instance as the operation declaring it, so no chain spends any
dependency wait budget -- which is what makes a `dist-upgrade` safe
inside one.

```python
chains = []
for instance_ref in instance_refs:
    update = sf_client.instance_execute(
        instance_ref, 'apt-get update', await_seconds=0)
    upgrade = sf_client.instance_execute(
        instance_ref, 'apt-get dist-upgrade -y',
        depends_on=[apiclient.agent_dependency(update)],
        await_seconds=0)
    chains.append((update, upgrade))

for update, upgrade in chains:
    try:
        sf_client.await_agent_chain([update, upgrade], timeout=1800)
    except apiclient.AgentOperationFailed as e:
        print(f'a node failed to upgrade, at {e.op_uuid}: {e}')
```

The nodes still work concurrently, because nothing orders one node
against another. Making them serial by giving each node's first
operation a `depends_on` edge to the previous node's last one would be
a single chain as deep as the fleet, and every link's budget would be
spent waiting for the nodes ahead of it. That is submit-await-submit
work, not one submission.

## Example applications

Real client applications which exercise this feature set, as living
documentation for the model above. Each repository has its own CI;
nothing in this repository runs them.

* **[client-python-k3s](https://github.com/shakenfist/client-python-k3s)**
  orchestrates k3s clusters over the agent side channel, keeping
  cluster state in namespace metadata. It is the broadest existing user
  of agent operations: `Cluster.execute_and_await()` fans a command out
  across every node of a cluster and then awaits the specific set of
  operations it submitted -- rather than everything queued against
  those instances, which may include another caller's work -- and
  `Cluster.await_idle()` treats `abort` as a failure alongside `error`
  and `expired`, while waiting out any state it does not recognise
  rather than declaring an instance idle. It does not declare
  dependency edges today; the fate sharing above is what a chain would
  buy it.

A second example application is tracked as
[issue #4825](https://github.com/shakenfist/shakenfist/issues/4825).
