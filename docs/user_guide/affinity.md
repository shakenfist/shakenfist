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

Shaken Fist emits a series of events while making a scheduling decision for an
instance, and those events are useful for debugging affinity operations. You can
see the events for an instance with this command:

`sf-client instance events ...uuid...`

You can of course see the currently set metadata for an instance with the
`sf-client instance show` command.