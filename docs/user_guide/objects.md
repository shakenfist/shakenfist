# Objects

Everything that you interact with in Shaken Fist is an object. Objects are
almost always referred to by a UUID (specifically a version 4 UUID) as a
string. The exceptions are: `node`s; `namespace`s; and `key`s within a namespace.

Terms used here are defined in the [glossary](../glossary.md).

In general an object is referred to in the API or on the command line "by
reference", which means you can either pass the object's name or its UUID to the
command. So for example if we had an instance with the UUID
0a38d51e-2f72-4848-80fb-03031978633b named "mikal", then we could run either of
the commands below to the same effect:

```
sf-client instance show 0a38d51e-2f72-4848-80fb-03031978633b
sf-client instance show mikal
```

In the case where you refer to an object by name, a lookup occurs of all
objects visible to you (those in your namespace, and namespaces that trust
your namespace). Additionally, shared artifacts are included if you're using
an artifact command.

Your own namespace always wins. Names are only unique within a namespace, so
if you have an artifact called `debian-11` and somebody else shares a
different artifact by that name, the name still means yours. Nothing anyone
else does to their objects can change what a name already means to you.

It is possible that the name you're using isn't unique. For example there might
be two instances named "mikal" with different UUIDs. In that case, you will get
an error indicating that there was more than one object which matched, and you'll
need to use a UUID to refer to the object. This also happens when two objects
you can see but do not own share a name — say a shared artifact and one in a
namespace which trusts you. You cannot resolve that by naming a namespace,
because you may only name your own, so use the UUID.

!!! warning "Commands which change something are narrower"

    The wider search applies when you are *reading*. Anything which changes an
    object — deleting it, sharing it, setting metadata — works only on objects
    in your own namespace, and resolves a name there and nowhere else. Being
    able to see somebody's object does not mean being able to change it, and
    that is true whether you use a name or a UUID.

!!! note

    Today only artifacts search beyond your own namespace by name. Instances
    and networks in a namespace which trusts you have to be referred to by
    UUID.
