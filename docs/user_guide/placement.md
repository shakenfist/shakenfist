# Checking whether instances would fit

Before creating a number of instances, particularly large ones or a group
which must all exist for the work to be useful, you can ask the cluster
whether there is room for them. This is a placement query. It runs the same
scheduler checks a real create does, but creates nothing.

Whether the cluster advertises the feature can be seen in its capabilities
list: the token is `placement-query`.

## Describing what you want

You describe each instance by its **shape**, which uses the same words as an
instance create:

| Key | Meaning |
|-----|---------|
| `cpus` | Number of vCPUs. Required. |
| `memory` | Memory in megabytes. Required. |
| `disk` | Total disk in gigabytes. Optional; omit for a shape that asks for no disk. |
| `disk_spec` | The list of disks, in the form an instance create takes. Optional. Give `disk` or `disk_spec`, not both. |
| `affinity` | An affinity document, as described in [Affinity](affinity.md). Optional. |
| `count` | How many of this shape. Optional, defaults to 1. |

Unknown keys are rejected. You can ask about at most 64 instances in one
query, counting each shape's `count`.

The shapes are considered as a set, in order. Each is placed as if the ones
before it already existed, so a `count` of 3 asks whether there is room for
three, not for one.

## From Python

```python
# client is a shakenfist_client API client you have already constructed.
answer = client.check_placement([
    {'cpus': 4, 'memory': 8192, 'disk': 40, 'count': 2},
    {'cpus': 2, 'memory': 4096},
])
```

`check_placement()` needs client-python v0.8.5 or newer. It takes a list of
shape mappings and returns the parsed answer. A set that does not fit is a
successful call which returns `fits: False`; it does not raise.

It does raise `IncapableException` when the cluster does not advertise the
capability, rather than sending a request that an older server would answer
with a 404. No request is sent in that case.

## Reading the answer

If the whole set would fit, the answer is `{"fits": true}`.

If it would not, the answer is a normal successful response which says why:

```
{"fits": false, "stage": "sufficient_idle_memory", "shape": 0}
```

`stage` names the scheduler check which ruled out every remaining hypervisor,
and `shape` is the index of the instance being placed at the time, counting
each shape `count` times. It is null if the refusal did not come from placing
one of your shapes. A hard affinity constraint which cannot be satisfied
names a different stage to one that merely ran out of capacity, so you can tell
"the cluster is full" from "nothing satisfies the constraint you asked for".
Placement is all or nothing: a set that does not fit has no partial answer.

The stages a query can name are the scheduler's filter and affinity checks.
It will never answer `capacity_guard`: that is the ledger check a create
makes when it actually claims the capacity, and only a create can meet it. So
a create can still be refused at `capacity_guard` moments after a query said
the set would fit — the room was there when you asked and somebody else took
it, which is what "advisory" means below.

Cluster administrators additionally receive a `shapes` list, in the order
asked, naming the hypervisor each shape would land on and its fallbacks.
Other users are not told which hypervisors exist, only the verdict.

## What the answer does and does not mean

The answer is **advisory**. Nothing is reserved by asking, and the cluster
may change before you create anything: another user may take the room, so a
`fits` of true can be out of date by your next request. A create is still
admitted or refused on its own merits. Use the query to decide whether to
try, not as a guarantee.

Asking creates no instances, changes no capacity accounting and leaves no
events against any instance.

If the database cannot be reached the query fails with a 503 and can be
retried.
