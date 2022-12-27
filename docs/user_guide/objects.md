# Objects

Everything that you interact with in Shaken Fist is an object. Objects are
almost always referred to by a UUID (specifically a version 4 UUID) as a
string. The exceptions are: `node`s; `namespace`s; and `key`s within a namespace.

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

It is possible that the name you're using isn't unique. For example there might
be two instances named "mikal" with different UUIDs. In that case, you will get
an error indicating that there was more than one object which matched, and you'll
need to use a UUID to refer to the object.