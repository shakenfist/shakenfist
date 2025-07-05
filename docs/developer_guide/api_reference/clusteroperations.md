# Cluster operations

Much like [agent operations](/developer_guide/api_reference/agentoperations),
since v0.8 Shaken Fist has exposed its internal work queuing system via the REST
API to external viewers. Internal work is queued with a series of objects called
cluster operations. While you cannot directly create a cluster operation,
being able to see the internal processes Shaken Fist is using to complete
requests is quite useful, especially if you're trying to determine what the
cluster is currently doing.

In general, when a Shaken Fist component wants to request another Shaken Fist
component perform an action, a cluster operation is created and queued in the
database. That other component is regularly polling for work to complete, and
will execute an operation as soon as it has an opportunity and the dependencies
for that work have been met. An important edge case is that the Shaken Fist
component can also queue work for itself, if that work is going to take longer
than the component is willing to wait while performing its primary request. So
for example if you request an instance start, a series of cluster operations
will be created to do things like fetch the required images, plug into the
virtual network, and create the actual instance.

Cluster operations cannot be directly looked up. They are accessible from the
objects they act upon, and are currently exposed for artifacts, instances, and networks.