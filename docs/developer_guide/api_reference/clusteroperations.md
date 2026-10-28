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

## Looking up cluster operations

Cluster operations can be looked up by uuid (across all object types), as a
list scoped to a specific target object, or as the transitive `depends_on`
chain ending at a given op. They are also accessible as sub-resources of the
objects they act upon, currently exposed for artifacts, instances, and
networks.

The per-object sub-resources are `/artifacts/{artifact_ref}/clusteroperations`,
`/instances/{instance_ref}/clusteroperations` and
`/networks/{network_ref}/clusteroperations`. Each lists the operations which
target that object, newest first, and each is an owner's route: the caller's
namespace must own the object, or be system. Sharing an artifact, or trusting
a namespace, makes the artifact readable but not its operations, because a
fetch operation names the instance whose boot caused it. An operation which only targets another
object is not listed, even if one of this object's operations waits for it: a
network reconcile an instance start waits on appears in the network's list, not
the instance's, and `GET /clusteroperations/{op_uuid}/chain` traces what an
operation waits on. Every entry carries an `observational` boolean, as
described below. By default only operations which are queued, in preflight or
executing are listed. With `all=true` the list also includes finished
operations which have not yet been removed. Finished operations are
eligible for removal thirty seconds after finishing and are removed by the
[deleted-object sweep](../../operator_guide/database.md#the-deleted-object-sweep),
and errored operations are kept for up to
`CLUSTER_OPERATION_TARGET_RETENTION`, seven days by default, so `all=true` is
a bounded window onto recent work and not a history.

???+ tip "REST API calls"

    * [GET /clusteroperations/{operation_type}/{operation_uuid}](https://openapi.shakenfist.com/#/clusteroperations/get_clusteroperations__operation_type___operation_uuid_):
      Retrieve a single cluster operation by its type and uuid. This is the
      polling endpoint a client uses to wait for an asynchronous operation
      (returned in the body of a 202 response) to reach a terminal state.
      Namespace-scoped: a non-admin caller must own every object the
      operation targets, and receives HTTP 403 for an operation which
      targets an object in another namespace, an object with no namespace
      (a blob, an interface or a node), or no recorded object at all.
    * [GET /clusteroperations/{op_uuid}/chain](https://openapi.shakenfist.com/#/clusteroperations/get_clusteroperations__op_uuid__chain):
      Return the transitive `depends_on` ancestor closure for an operation,
      ordered newest-first. Useful for tracing which step in a chained
      workflow failed. Namespace-scoped: non-admin callers receive HTTP 403
      if any chain member belongs to a foreign namespace.
    * [GET /clusteroperations?target_object_type={type}&target_uuid={uuid}](https://openapi.shakenfist.com/#/clusteroperations/get_clusteroperations):
      List all cluster operations that targeted a given object. The
      `target_object_type` query parameter is a Shaken Fist object type
      string (e.g. `network`, `instance`, `artifact`). The caller's
      namespace must own the target object, or the caller must be admin.
      Each op in the response carries an `observational` boolean: true
      when every task of the op only observes the target (a checksum or
      health check) rather than changing it. Observational ops are
      listed here but do not count towards the object's
      `last_cluster_operation` or its pending operations.

## Waiting for an object to settle

Instance, network and artifact views carry a `has_pending_operations` boolean
beside `last_cluster_operation`. It is true while any non-observational
cluster operation targeting the object is queued, in preflight or executing,
and it is true when the server cannot tell, so a transient database problem
makes an object look busy rather than idle. Unlike `last_cluster_operation`,
it is not masked by a later operation which has already finished. It is also
present on the objects returned by the list endpoints. The field, and the
per-object `/clusteroperations` listings which report every operation
targeting the object, each with an `observational` key, are advertised by the
`object-pending-operations` capability.

Observational operations never make an object pending. A background sweep, or
a checksum verification requested through
`GET /blobs/{blob_uuid}/checksum/{algorithm}`, is listed with
`observational: true` but leaves `has_pending_operations` false. A caller
waiting for a requested blob hash therefore cannot use this field: it polls
`GET /blobs/{blob_uuid}/checksum/{algorithm}` instead, which returns the hash
once it has been recorded and null until then, and queues a verification
if the hash is not yet known.

??? example "Python API client: wait for an instance to settle"

    ```python
    import time
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    if not sf_client.check_capability('object-pending-operations'):
        raise Exception('this cluster does not report pending operations')

    while sf_client.get_instance(instance_uuid)['has_pending_operations']:
        time.sleep(1)
    ```

## Polling an asynchronous operation

Some REST endpoints (currently `DELETE /networks/{network_ref}` and
`DELETE /networks`) return HTTP 202 (Accepted) with an op handle in the
body rather than performing the work synchronously. The handle has the
shape `{"op_type": "net_op", "op_uuid": "<uuid>"}`. Clients that need
synchronous-completion semantics should poll
`GET /clusteroperations/{op_type}/{op_uuid}` until the op's `state`
field is in a terminal set (`complete`, `abort`, `deleted`, or `error`).
On `error`, the op's `external_view` carries an `error_report` field
with structured failure information.

The Python client library handles this transparently by default — see
`delete_network` and `delete_all_networks` in the `shakenfist_client`
package, which accept a `wait=True/False` kwarg (defaulting to `True`)
controlling whether the client polls until terminal or returns the op
handle immediately.

??? example "Python API client: trace the chain that spawned an op"
    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    chain = sf_client.get_cluster_operation_chain(
        '22b757ae-41d1-456b-84a4-edca694ceb6d')
    for op in chain:
        print(op['uuid'], op['state'], op['tasks'])
    ```

??? example "Python API client: list ops targeting a network"
    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    ops = sf_client.list_cluster_operations_for_target(
        'network', '6a663ac7-620f-417c-b197-3004d8c84bc7')
    for op in ops:
        print(op['uuid'], op['operation_type'], op['state'])
    ```
