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

???+ tip "REST API calls"

    * [GET /clusteroperations/{operation_type}/{operation_uuid}](https://openapi.shakenfist.com/#/clusteroperations/get_clusteroperations__operation_type___operation_uuid_):
      Retrieve a single cluster operation by its type and uuid. This is the
      polling endpoint a client uses to wait for an asynchronous operation
      (returned in the body of a 202 response) to reach a terminal state.
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
