# Events

Events are Shaken Fist's audit log mechanism. Many operations, ranging from
creation and subsequent use of an authentication token, to any change in the data
for an object, will result in an event being created in the event log for the
relevant object. Importantly, regular "usage events" are also emitted, which we
expect would form the basis for a consumption based billing system.

Events may be requested using the `sf-client ...object... events` command, for
example `sf-client artifact events ...uuid...` will return the events for the
relevant artifact.

The schema for events is still in flux, so be careful implementing automated
systems which consume events. This will remain true until we are more confident
that all relevant audit lock events are being collected. At that point we will
standardize and stabilize the interface.

As of v0.7, each event log entry has a type. The currently implemented types
are:

* audit: audit log entries such as object creation or deletion, and authentication.
* mutate: object modifications which are not an audit log entry, such as minor updates.
* status: status messages useful to a user, such as progress of fetching an image.
* usage: billing information.
* resources: cluster resource usage information of interest to an operator.
* prune: messages relating to pruning of other message types.
* historic: events from before the type system was introduced.

For each of these types, an operator can configure a retention period. The
default periods are:

* audit (MAX_AUDIT_EVENT_AGE): 90 days.
* mutate (MAX_MUTATE_EVENT_AGE): 90 days.
* status (MAX_STATUS_EVENT_AGE): 7 days.
* usage (MAX_USAGE_EVENT_AGE): 30 days.
* resources (MAX_RESOURCES_EVENT_AGE): 7 days.
* prune (MAX_PRUNE_EVENT_AGE): 30 days.
* historic (MAX_HISTORIC_EVENT_AGE): 90 days.

To permanently retain a type of event log entry, set the corresponding configuration
value to -1.