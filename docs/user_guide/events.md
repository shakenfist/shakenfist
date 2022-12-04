# Events

Events are Shaken Fist's audit log mechanism. Many operations, ranging from
creation and subsequent use of an authentication token, to any change in the data
for an object, will result in an event being created in the audit log for the
relevant object. Importantly, regular "usage events" are also emitted, which we
expect would form the basis for a consumption based billing system.

Events may be requested using the `sf-client ...object... events` command, for
example `sf-client artifact events ...uuid...` will return the events for the
relevant artifact.

The schema for events is still in flux, so be careful implementing automated
systems which consume events. This will remain true until we are more confident
that all relevant audit lock events are being collected. At that point we will
standardize and stabilize the interface.