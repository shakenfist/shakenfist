# Object metadata

All objects in Shaken Fist support a simple metadata system. This system is
presented as a series of key, value pairs which are stored against the
relevant object. A worked example can be seen in the [description of instance
affinity](/user_guide/affinity/) which requires specific keys and formats for
their values, but you're not limited to that -- other keys can have data of any
format that you can express in an API call.

???+ note

    It is not intended that you store large amounts of data in a metadata key.
    If you want to store more than a couple of kilobytes in a value, then instead
    store a reference to an external system or a blob which contains the data.

You can set a metadata key's value on the command line like this:

`sf-client instance set-metadata ...uuid... key-name key-value`

Metadata values are show in the `show` output for the various object types, and
there is no separate command to look them up. You can also delete a metadata key
like this:

`sf-client instance delete-metadata ...uuid... key-name`