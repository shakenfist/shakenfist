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

???+ info

    Under the hood, Shaken Fist filters possible candidate hypervisors based on
    the affinity coefficients specified. Only tags from within your namespace are
    considered for this filtration. This decision is only made on the original
    start up of an instance, and does not apply later. That is, if you change
    the tags or affinity of an instance after instance creation it will not
    affect that instance in any way, although it might affect scheduling decisions
    for future instances.

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

`require_with_tag` and `require_without_tag` are **hard**. A hypervisor
which does not satisfy them cannot host the instance at all, so they are
applied alongside the CPU, memory and disk filters rather than as a
ranking. If no hypervisor satisfies them, the create fails with a **409
Conflict** naming the constraint -- where the weighted form would have
silently placed the instance anywhere.

`prefer_with_tag` and `prefer_without_tag` are **soft**, and behave exactly
as weights did: they rank the hypervisors that survived the hard filters.

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