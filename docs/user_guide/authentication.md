# Authentication

While there is a detailed discussion of the Shaken Fist authentication system
in the [developer guide](/developer_guide/authentication/), that is likely more
detail than a day to day user of Shaken Fist is interested in. This page
therefore provides the details in a simpler and more direct form.

As a user of Shaken Fist, the administrator of the cluster you are using will
have created a _namespace_ to contain the resources you create in Shaken Fist.
This namespace can have several authentication keys associated with it, which
are simply strings you pass to Shaken Fist to prove your identity, much like API
keys for GitHub or other web services. Normally your administrator will create
a key per user, but its also possible to create a key per system -- there are
not real rules imposed by Shaken Fist on when you should create a key not reuse
an existing one.

For most users, this key will be provided in the form of a file you should place
at `.shakenfist` in your home direct. An example file might be:

```
{
    "namespace": "mynamespace",
    "key": "oisoSe7T",
    "apiurl": "https://shakenfist/api"
}
```

This file specifies your namespace, the key you will use to authenticate, and
the location of the API server for that Shaken Fist cluster.

Once you have that file in the correct location, the the Shaken Fist command line
client and API client will function correctly with no further configuration
required.