# Agent Operations

An agent operation is a request handled by the in-guest `sf-agent2`
process rather than by the hypervisor directly: executing a command,
copying a blob into the instance, or fetching a file out of it. The
API server creates an `AgentOperation` object for each request, which
is enqueued against the target instance and dispatched by the
sidechannel daemon once the instance's agent channel is ready. For the
request parameters themselves -- `deadline_seconds`,
`progress_timeout_seconds`, and what each of the three creating calls
accepts -- see
[the API reference](../developer_guide/api_reference/instances.md#bounding-how-long-an-agent-operation-may-take).
This page is about what happens operationally once a request is
enqueued.

## One executor per instance

The sidechannel daemon runs at most one executor thread per instance
at a time: its dispatch loop skips any instance that already has a
live executor, and `Instance.agent_operation_next()` leaves an
executing operation at the head of the queue rather than handing out a
second one. This is deliberate -- it is what makes dispatch crash
safe, since the queue entry survives a daemon restart -- but it has an
operational consequence worth knowing: an operation that wedges takes
out that instance's *entire* agent surface until something ends it.
Every other queued operation against the same instance, however
unrelated, waits behind it. Nothing about this is per-command; it is
per-instance.

This is why the timing budgets below exist, and why the reaper further
down exists too: a wedged operation with no budget and no reaper would
block an instance's agent operations indefinitely.

## The two budgets

Every agent operation carries two independent timing budgets, either
of which the caller may set explicitly per request. If they do not,
the server applies a default.

- **`AGENT_OPERATION_DEFAULT_DEADLINE`** (default 600 seconds,
  `shakenfist/config.py:240`) is a wall-clock budget. It is counted
  from the moment the API server received the request, not from when
  the agent picked the work up -- so time spent queued behind another
  operation on the same instance, and any preflight work such as
  fetching a blob onto the hypervisor, both count against it before
  the operation ever executes.
- **`AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT`** (default 30 seconds,
  `shakenfist/config.py:259`) is how long a transfer may go without
  making forward progress. It applies only to the commands that can
  report progress at all -- the transfers behind `agent/get` and
  `agent/put` -- and never to `agent/execute`: nothing an executed
  command does is observable as progress, so the API refuses a
  `progress_timeout_seconds` on that endpoint rather than accept one
  that could never fire.

A caller may send `0` for either parameter to disable it entirely;
omitting the parameter takes the default instead. See the API
reference linked above for the full three-way distinction between
omitted, zero and a positive value.

Both parameters are bounded by **`AGENT_OPERATION_MAX_DEADLINE`**
(default 86400 seconds -- one day, `shakenfist/config.py:320`), an
operator ceiling published as their `maximum` in the API specification
and refused with a 400 above it. The same ceiling is the backstop for
an operation whose caller disabled both budgets at once, which every
`agent/execute` created with `deadline_seconds=0` is: with no budget
left anywhere, a single request could otherwise park an instance's
only executor slot for as long as the guest command cared to run
(issue 4074). Such an operation is expired
`AGENT_OPERATION_MAX_DEADLINE` seconds after it last changed state. A
`deadline_seconds` of `0` alongside a live progress timeout still
means no wall-clock deadline at all, so a very large transfer which
keeps making progress is never expired by the ceiling.

## Where the budgets are enforced

Three points enforce these budgets, so an operation that has run out
of time is retired wherever it happens to be sitting rather than only
when something next looks at it directly:

1. **At dequeue** -- `Instance.agent_operation_next()` expires a
   queued head whose deadline has already passed before it is ever
   handed to an executor, and moves on to consider the next entry in
   the same pass.
2. **During preflight** -- the task that promotes an operation from
   `preflight` to `queued` (fetching a blob onto the hypervisor, for
   `agent/put`) also checks the deadline either side of that work.
3. **Inside the executor** -- once running, the executor checks both
   budgets roughly once a second.

`agent/execute` never goes through preflight at all; the endpoint
queues it directly. So if you see an expired `execute` operation, it
was expired either at dequeue (it sat in the queue too long) or inside
the executor (it ran too long) -- never during preflight, because that
stage does not apply to it.

## Telling `expired` from `error`

An agent operation that fails ends in `error`. An agent operation that
runs out of one of the two budgets above ends in `expired` -- a
distinct terminal state. The distinction matters operationally:
`error` means the operation itself went wrong (the agent reported a
failure, the command was unrecognised); `expired` means a budget the
caller (or the server default) set simply ran out.

*Which* budget expired an operation is not on the operation's own
external view -- it is in the state row's message, which is not
surfaced as a separate field. The reliable place to read it is the
instance's event log: `expire()` always writes an audit event against
both the operation and the instance recording the reason, and because
an expired operation is swept for hard deletion once the cleaner's
delay elapses (the same as a completed one), the instance's copy of
that event is the one that survives to be read later. When
`instance execute`/`upload`/`download` on a current client hits a
terminal state, it now raises `AgentOperationFailed` immediately
rather than continuing to poll -- see the client behaviour note in the
[v0.7 to v0.8 release notes](../release_notes/v07-v08.md).

## Retry and the executor reaper

An operation that fails while `executing`, for a retryable reason, is
not necessarily abandoned. It can be returned to the *head* of its
instance's queue for another attempt, provided all of the following
hold:

- **The failure is retryable at all.** Retryability is a property of
  the whole command list for the operation, not of whichever command
  happened to be in flight when the attempt was abandoned -- because a
  retry restarts the command list from index 0, and re-running an
  earlier command in the list would repeat a side effect the agent
  cannot take back. `agent/execute` operations are never retryable for
  exactly this reason: executing a command is a side effect that
  cannot be undone, so re-running it from the start would double it.
  `agent/get` and `agent/put` are retryable.
- **The wall-clock deadline has not passed.** A stalled attempt can be
  retried; an operation whose caller-set deadline has already been
  used up cannot -- there is no time left for a further attempt to
  deliver anything in, so it goes straight to `expired` instead.
- **The attempt cap has not been reached.** Bounded by
  `AGENT_OPERATION_MAX_ATTEMPTS` (default 3, `shakenfist/config.py:303`)
  -- the number of times an operation may be dispatched to the agent
  in total, counting the first attempt plus retries. Once that many
  attempts have been made, the operation is retired instead of tried
  again.

When none of those conditions can be met, the operation reaches a
terminal state instead: `expired` for a stall that cannot be retried
(the caller's timing budget is still the reason), `error` for the case
where an executor simply went away with nothing else to blame it on.

Separately, a **node-local reaper**
(`Monitor.reap_instance_executors()`,
`shakenfist/daemons/sidechannel/main.py:1559`) runs on every dispatch
pass and resolves operations that the queue itself cannot tell are
stuck. Nothing in the queue can distinguish a live executor from a
dead one, but the node the instance is placed on can, because the
executor is a thread in that node's own process. The reaper recovers
three situations:

- **An executor thread that died without resolving its operation** --
  the sidechannel daemon restarted while an operation was executing
  (so no `finally` block ever ran), or a dead thread was swept without
  cleaning up after itself. The operation is `fail()`-ed: something
  was actively running it and stopped being able to, which is treated
  as an executor failure rather than a budget running out.
- **An operation left `executing` with no executor at all**, most
  commonly a daemon restart. Same outcome as above: `fail()`, via
  `resolve_abandoned_operation()`, so it may still retry if the usual
  retry conditions hold.
- **An executor wedged before it ever connected to the agent**, once
  its operation's deadline has passed. This is the one case the
  executor's own budget checks cannot catch, because it only checks
  its budgets once it is running its main loop -- a hang in the
  pre-connection wait never reaches that code. The reaper detects it
  from the outside, purely on the deadline having passed, and
  `expire()`s the operation before aborting the wedged executor
  thread.

The reaper is rate-limited to once every 30 seconds per instance, and
cannot help in two situations by design: an instance with no live
monitor (it waits for the monitor to restart instead, normally within
30 seconds), and an operation created with `deadline_seconds=0`
alongside a live progress timeout whose executor wedges before
connecting -- with no wall-clock budget, there is no evidence
available to declare it stuck. (An operation with *both* budgets
disabled is not in this hole: the `AGENT_OPERATION_MAX_DEADLINE`
backstop gives it a wall-clock deadline the reaper can act on.)

## What to tune, and when

The most likely complaint you will see after upgrading is a long agent
command being killed partway through. This is expected: the effective
default deadline (600 seconds, counted from request receipt) is
*tighter* than the fixed 900-second backstop it replaced, which only
started counting once the executor connected. A command that used to
comfortably fit under 900 seconds of execution time can now be cut off
by queue time and preflight time eating into its 600-second budget
before it even starts running.

If that happens:

- **For a one-off long command**, pass an explicit `deadline_seconds`
  on the request rather than changing cluster-wide configuration.
- **If long agent commands are routine for your workload**, raise
  `AGENT_OPERATION_DEFAULT_DEADLINE`. The trade-off is that a genuinely
  wedged operation -- one that never made progress and never will --
  now occupies its instance's single executor slot for longer before
  anything notices, since the wall-clock deadline is the backstop that
  eventually catches a hang the progress timeout cannot see (an
  `agent/execute` in particular has no progress signal at all).
- **`AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT`** rarely needs raising
  for the same reason: it only governs transfers that report progress,
  and 30 seconds is generous relative to real transfer times. Raise it
  only if you have evidence of transfers that are healthy but slow
  enough to trip it -- a very constrained network path, for example.
- **`AGENT_OPERATION_MAX_ATTEMPTS`** is a retry budget, not a timing
  budget; raising it gives a flaky agent channel more chances to
  recover but also lets a genuinely broken operation occupy an
  instance's executor slot for more attempts before it is finally
  retired.
- **`AGENT_OPERATION_MAX_DEADLINE`** is the ceiling on what a caller
  may request, and the backstop for an operation with no budgets at
  all. Lower it if a day is more executor-slot parking than you are
  willing to let one request buy; keep it at least
  `AGENT_OPERATION_DEFAULT_DEADLINE`, or an omitted `deadline_seconds`
  is granted more time than an explicit one may ask for.
