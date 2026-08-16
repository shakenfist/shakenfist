# Authentication

While there is a detailed discussion of the Shaken Fist authentication system
in the [developer guide](/developer_guide/authentication/), that is likely more
detail than a day to day user of Shaken Fist is interested in. This page
therefore provides the details in a simpler and more direct form.

Terms used here are defined in the [glossary](../glossary.md).

As a user of Shaken Fist, the administrator of the cluster you are using will
have created a _namespace_ to contain the resources you create in Shaken Fist.
This namespace can have several authentication keys associated with it, which
are simply strings you pass to Shaken Fist to prove your identity, much like API
keys for GitHub or other web services. Normally your administrator will create
a key per user, but it's also possible to create a key per system -- there are
no real rules imposed by Shaken Fist on when you should create a key or reuse
an existing one.

For most users, this key will be provided in the form of a file you should place
at `.shakenfist` in your home directory. An example file might be:

```
{
    "namespace": "mynamespace",
    "key": "oisoSe7T",
    "apiurl": "https://shakenfist/api"
}
```

This file specifies your namespace, the key you will use to authenticate, and
the location of the API server for that Shaken Fist cluster.

Once you have that file in the correct location, the Shaken Fist command line
client and API client will function correctly with no further configuration
required.

## Keys the cluster generated for you

If your administrator asked Shaken Fist to generate your key rather than
choosing one, it will look like this:

```
sfk_e57SPWpK3JGmyhuYLrcUtSwhtdJlONiXzzzzzz
```

That example is deliberately not a usable key. The last six characters
are a checksum, and `zzzzzz` is a larger number than any checksum the
cluster can produce, so the example fails validation before anything
tries it. A real key ends in six characters which look as random as the
rest of it.

The `sfk_` prefix is there so that a leaked credential is easy to find --
in a log file, in a repository, or by an automated secret scanner. Treat it
exactly as you would any other key. There is nothing you need to do
differently, but it is worth recognising the shape, because a string starting
`sfk_` in a place you did not expect is a credential that has escaped.

A generated key is shown to you once, when it is created. Shaken Fist stores
only a hash of it, so nobody -- including your administrator -- can read it
back to you later. If you lose it, you need a new one.

## Your key may expire

A key can be created with an expiry, and many are. A key with no expiry set
never expires, which is the case for most keys created by hand.

When a key expires it stops working immediately. There is no grace period.
What you will see is a `401 Unauthorized` from a credential that worked
yesterday, with nothing else having changed.

There is one small wrinkle. Your client does not send your key on every
request; it trades the key for a short lived token, nominally good for fifteen
minutes, and sends that. So a key which expires part way through a session can
leave you working for a few more minutes on the token you already hold, and
then failing. If a long running job stops with a 401 shortly after it seemed
fine, an expired key is the first thing to check.

## Your key may be limited in what it can do

A key can carry _scopes_, which name the kinds of operation it is allowed to
perform -- reading blobs, say, but not deleting instances. A key with no
scopes recorded can do anything its namespace can do, which is how keys
behaved before scopes existed and how most hand-created keys still behave.

If your key is scoped, an operation outside those scopes is refused with a
`403 Forbidden` and the message `token is not scoped for this operation`.

The distinction that matters when something is refused:

* **403** means you are authenticated and in the right namespace, but this
  key is not permitted to do this particular thing. Ask for a key with wider
  scopes, or use a different one.
* **404** on an object you believe exists usually means it is in a namespace
  you cannot see. Shaken Fist deliberately answers 404 rather than 403 here,
  so that a refusal does not reveal which namespaces exist.

## Keys that came from somewhere else

Shaken Fist can issue a key to a workload that already has an identity
elsewhere -- most commonly a CI job, which can prove to Shaken Fist that it is
a particular workflow in a particular repository and receive a key in return.
This means an automated job does not need a long lived Shaken Fist key stored
alongside it.

If you are handed one of these, there is nothing special about it in use. It
is an ordinary namespace key: it goes in the same config file, authenticates
the same way, and is subject to the same scopes and expiry as any other. It
will simply be one that was created automatically, is scoped narrowly, and
expires fairly quickly.

Setting this up is an administrator's job, and is covered in the
[operator guide](/operator_guide/authentication/#federated-identity).

## What you cannot currently see

There is presently no way to ask Shaken Fist what your own key's expiry or
scopes are. `sf-client namespace show <namespace>` lists the *names* of the
keys in your namespace and nothing further about them, and there is no API
which returns the rest.

In practice this means that when a key stops working, the status code is your
diagnostic -- 401 for expired, 403 for out of scope -- and your namespace
owner or cluster administrator is the source of truth for what a key was
created with. If you are chasing an intermittent authentication failure, ask
them to check the expiry rather than trying to determine it yourself.
