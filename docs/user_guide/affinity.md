# Affinity

There are times when you want to express a preference (or anti-preference) for
two instances sharing a hypervisor. For example, you might have two IO intensive
instances and want to keep them apart, or you might have two instances which
talk a lot over the network to each other and want to keep them together.

Shaken Fist supports a simple affinity system for this use case. The system is
built on top of an instance metadata entry called `tags`, where you specify the
attributes of the instance. This metadata entry must be formatted as a valid
JSON list. So for example we might set the following simple tag on an instance:

```
["webserver"]
```

Or on the command line:

`sf-client instance set-metadata ...uuid... tags '["webserver"]'`

Affinity is then set via the `affinity` metadata entry, and consists of a JSON
dictionary specifying the weight to give to a given tag in scheduling decisions.
In our current example, let's say we want to try quite hard to not have more than
one of our web servers on a given hypervisor. We might therefore write an
affinity metadata entry like this:

```
{
    "webserver": -10
}
```

Or on the command line:

`sf-client instance set-metadata ...uuid... affinity '{"webserver": -10}'`

There are no fixed rules for what the maximum and minimum numbers for this
preference value are, although generally we recommend they range from -100 to 100,
where 100 means you'd really really love to be on the same hypervisor, and -100
means you'd be very unhappy to be on the same hypervisor.

!!! warning

    This weighted form is **deprecated**, and the magnitude is already
    ignored by the scheduler -- only the sign is read. It still works and
    will for at least one more release, but new specifications should use
    the constraint form described in
    [Constraints, not just preferences](#constraints-not-just-preferences).
    See [The weighted form is deprecated](#the-weighted-form-is-deprecated).

???+ info

    Under the hood, weights **rank** the candidate hypervisors rather than
    filtering them. Every hypervisor which can host your instance at all
    remains a candidate whatever the weights say; the weights decide which
    of them is preferred. That distinction is the whole of the next
    section, and it is the difference between a preference and a promise.
    The constraint form described below does filter, and is how you ask
    for a hypervisor to be ruled out rather than merely disfavoured.

    Only tags from within your namespace are considered, for both forms.

    The specification is re-read **every time the instance is scheduled**,
    which includes every restart and reschedule and not only the original
    create. So a specification you change later applies from the next
    restart onwards, and a hard constraint no hypervisor can satisfy by
    then will refuse that restart -- after trying every hypervisor in the
    cluster, not only the one it was on. If you have an instance which
    must always be able to start, prefer `prefer_*` over `require_*`: a
    ranking with nothing to rank is harmless, and a constraint with
    nothing to satisfy it is not.

You can of course have more than one tag and affinity preference set at a time.
So to extend our example, let's say that web servers do not prefer sharing with
other web servers, but do like sharing with a cache server. You might write that
like this:

```
{
    "webserver": -10,
    "cache": 10
}
```

Or on the command line:

`sf-client instance set-metadata ...uuid... affinity '{"webserver": -10, "cache": 10}'`

## What a preference promises, and what it does not

A preference is consulted **when the scheduler has a choice**. It is not a
guarantee, and it is not a constraint.

That distinction matters more than it sounds. The scheduler first decides
which hypervisors *can* host your instance at all -- enough CPU, enough
memory, enough disk -- and only then ranks the survivors by affinity. If
that first step leaves exactly one candidate, there is no ranking to do:
your instance is placed on the only node available, whether your affinity
wanted it there or not.

So a single-candidate placement is **neither a preference honoured nor a
preference violated**. It is a placement made without consulting your
preference, because there was nothing to consult it about. If you look at
where two instances landed and conclude that affinity was ignored, check
first how many candidates the scheduler actually had.

The scheduling events tell you which happened. See
[the scheduler operator guide](/operator_guide/scheduler/) for how to read
`schedule have highest affinity` and `schedule final candidates` to tell
"scored wrong" apart from "had no choice".

## Constraints, not just preferences

Weights answer "would you rather", and there are times you need to answer
"you must". Affinity therefore accepts a second value shape, using four
reserved names instead of tag weights:

```
{
    "require_with_tag": ["database"],
    "require_without_tag": ["batch"],
    "prefer_with_tag": ["cache"],
    "prefer_without_tag": ["webserver"]
}
```

Or on the command line:

`sf-client instance set-metadata ...uuid... affinity '{"require_without_tag": ["batch"]}'`

Each name takes a JSON list of tags, and you may use any subset of the four.

All four match the tags of instances **already placed on a candidate
hypervisor**, and only instances in **your own namespace**:

| Name | Strength | Effect on a hypervisor hosting a matching instance |
|------|----------|---------------------------------------------------|
| `require_with_tag` | hard | Hypervisors hosting *no* matching instance cannot host yours at all |
| `require_without_tag` | hard | Hypervisors hosting a matching instance cannot host yours at all |
| `prefer_with_tag` | soft | +1 to the hypervisor's score, per matching instance |
| `prefer_without_tag` | soft | -1 from the hypervisor's score, per matching instance |

The hard pair is applied alongside the CPU, memory and disk filters rather
than as a ranking. If no hypervisor satisfies them, the create fails with a
**409 Conflict** naming the constraint -- where the weighted form would have
silently placed the instance anywhere. The soft pair behaves exactly as
weights did: it ranks the hypervisors that survived the hard filters.

???+ warning "Hard constraints are re-checked on every restart"

    A `require_*` constraint is not a create-time decision. The scheduler
    runs the same filter whenever the instance is scheduled, restarts and
    reschedules included, so an instance placed under
    `require_with_tag: ["database"]` can be refused later if the tagged
    neighbour it was placed beside has since been deleted. The refusal
    happens only after every hypervisor in the cluster has been tried, and
    the error names the constraint rather than reporting a full cluster.

    This is deliberate: a constraint which applied once and then stopped
    would be a create-time hint with a misleading name, and
    `require_without_tag` in particular is not something an instance stops
    needing on its second boot. But it does mean `require_*` trades some
    of an instance's ability to restart for the guarantee, where
    `prefer_*` does not.

Two things about that table are easy to read past, and both are covered in
detail below. The soft forms are terms in a **sum** and not soft vetoes, so
`prefer_without_tag` can be outvoted. And the namespace scope applies to the
hard forms too, so `require_without_tag` is **not** an isolation primitive.

???+ warning "`prefer_*` terms are a sum, not a veto"

    Each matching neighbouring instance contributes +1 (for
    `prefer_with_tag`) or -1 (for `prefer_without_tag`), and the totals are
    summed **across neighbours and across tags**. So `prefer_without_tag`
    is a term in a sum rather than a soft veto, and a match on it can be
    outvoted by neighbour count on the other axis.

    Concretely, with `prefer_with_tag: ["web"]` and
    `prefer_without_tag: ["batch"]` both set: a hypervisor hosting three
    `web` instances and one `batch` scores +2, and beats a hypervisor
    hosting one `web` and no `batch` at +1 -- even though the first one
    has the tag you asked to avoid. If you need the avoidance honoured
    regardless of neighbour count, use `require_without_tag`.

???+ info "`require_without_tag` is not an isolation primitive"

    Like the weighted form, the constraints only consider instances in
    **your own namespace**. `require_without_tag` will not keep your
    instance away from another tenant's workload, because it cannot see
    it. It is a placement constraint within your namespace, not a security
    or isolation boundary.

???+ warning "The first member of a `require_with_tag` group"

    The constraints match instances *already placed*, so the first
    instance of a group cannot be created under the constraint that
    defines the group. Ask for `require_with_tag: ["web"]` on a cluster
    where nothing in your namespace carries the `web` tag and every
    hypervisor is ejected, so you get a 409 -- including when the
    instance you are trying to create is the one that would carry the
    tag. This is the constraint working, not a bug, but it has no way
    out on its own.

    Seed the group first: create one instance carrying the tag and
    *without* the `require_with_tag` constraint, then create the rest
    with it. Or use `prefer_with_tag`, where having nothing to rank is
    harmless.

The two value shapes cannot be mixed in one specification. A dictionary of
tag weights is the weighted form; a dictionary using the four reserved
names is the constraint form; a dictionary containing both is rejected with
a 400, because either way of resolving it would silently discard half of
what you asked for.

## The weighted form is deprecated

Tag weights still work, and will keep working for at least one more
release. They are mapped onto the constraint form when the scheduler reads
them: a positive weight becomes `prefer_with_tag`, a negative weight
becomes `prefer_without_tag`, and zero becomes nothing, which is what zero
always meant.

**The magnitude is discarded by that mapping.** `{"webserver": -10}` and
`{"webserver": -100}` now behave identically. This is deliberate: a weight
was a multiplier on a count of neighbouring instances that you could not
predict when you wrote it, so two weights were never really comparable.
What replaces it is a quantity that is defined -- how many matching
neighbours a hypervisor has.

Ordering is unchanged whenever every weight in a specification shares a
magnitude, which includes every single-tag specification. Where magnitudes
differ, ordering can change: `{"a": 100, "b": 1}` maps to
`prefer_with_tag: ["a", "b"]`, so a hypervisor carrying only `b` now ties
with one carrying only `a`.

Setting a weighted specification records a deprecation event against the
instance. Note that this happens when the specification is **accepted**, so
instances that were already carrying a weighted specification before the
upgrade do not produce one -- see
[the scheduler operator guide](/operator_guide/scheduler/) for how to find
them.

## Debugging

Shaken Fist emits a series of events while making a scheduling decision for an
instance, and those events are useful for debugging affinity operations. You can
see the events for an instance with this command:

`sf-client instance events ...uuid...`

You can of course see the currently set metadata for an instance with the
`sf-client instance show` command.