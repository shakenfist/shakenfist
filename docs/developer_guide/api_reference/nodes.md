# Nodes (/nodes/)

Nodes are how Shaken Fist models the hypervisors that actually run your virtual
machines. They're a little unusual, as we use the host name to track the object
instead of an assigned UUID. Nodes are an administrative-only object, not
available to other users of Shaken Fist.

???+ note

    For a detailed reference on the state machine for nodes, see the
    [developer documentation on object states](/developer_guide/state_machine/#nodes).

## Node lifecycle

Nodes cannot be created via the API, instead nodes are created by installing
Shaken Fist on a machine and having that machine join a Shaken Fist cluster.
However, you can delete a node via API and that will cause the machine to delete
all the instances running and stop accepting new work.

???+ tip "REST API calls"

    * [GET /nodes](https://openapi.shakenfist.com/#/nodes/get_nodes): List all nodes in the cluster, including deleted nodes.
    * [DELETE /nodes/{node}](https://openapi.shakenfist.com/#/nodes/delete_nodes__node_): Delete a node.
    * [GET /nodes/{node}](https://openapi.shakenfist.com/#/nodes/get_nodes__node_): Get information about a single node.

??? example "Python API client: get details for a node"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    n = sf_client.get_node('sf-1')
    print(json.dumps(n, indent=4, sort_keys=True))
    ```

??? example "Python API client: list all nodes"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    n = sf_client.get_nodes()
    print(json.dumps(n, indent=4, sort_keys=True))
    ```

## Other network information

We can also request other information for a network. For example, we can list the
nodes's network interfaces, or the events for the network. See the
[user guide](/user_guide/events/) for a general introduction to the Shaken Fist
event system.

???+ tip "REST API calls"

    * [GET /nodes/{node}/events](https://openapi.shakenfist.com/#/nodes/get_nodes__node__events): Fetch events for a node.

??? example "Python API client: list events for a node"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    events = sf_client.get_node_events('sf-1')
    print(json.dumps(events, indent=4, sort_keys=True))
    ```

    Note that events are returned in reverse chronological order and are limited
    to the 100 most recent events.

    ```json
    [
        ...
        {
            "duration": null,
            "extra": {
                "cpu_available": 12,
                "cpu_load_1": 2.0,
                "cpu_load_15": 1.85,
                "cpu_load_5": 1.87,
                "cpu_max": 12,
                "cpu_max_per_instance": 16,
                "cpu_total_instance_cpu_time": 235110000000,
                "cpu_total_instance_vcpus": 1,
                "disk_free": 376334815232,
                ...
            },
            "fqdn": "sf-1",
            "message": "updated node resources and package versions",
            "timestamp": 1685475323.252612,
            "type": "resources"
        },
        ...
    ]
    ```

## Metadata

All objects exposed by the REST API may have metadata associated with them. This
metadata is for storing values that are of interest to the owner of the resources,
not Shaken Fist. Shaken Fist does not attempt to interpret these values at all,
with the exception of the [instance affinity metadata values](/user_guide/affinity/).
The metadata store is in the form of a key value store, and a general introduction
is available [in the user guide](/user_guide/metadata/).

???+ tip "REST API calls"

    * [GET ​/nodes/{node}​/metadata](https://openapi.shakenfist.com/#/nodes/get_nodes__node__metadata): Get metadata for a node.
    * [POST /nodes/{node}/metadata](https://openapi.shakenfist.com/#/nodes/post_nodes__node__metadata): Create a new metadata key for a node.
    * [DELETE /nodes/{node}/metadata/{key}](https://openapi.shakenfist.com/#/nodes/delete_nodes__node__metadata__key_): Delete a specific metadata key for a node.
    * [PUT /nodes/{node}/metadata/{key}](https://openapi.shakenfist.com/#/nodes/put_nodes__node__metadata__key_): Update an existing metadata key for a node.

??? example "Python API client: set metadata on a node"

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    sf_client.set_node_metadata_item(net_uuid, 'foo', 'bar')
    ```

??? example "Python API client: get metadata for a node"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    md = sf_client.get_node_metadata(net_uuid)
    print(json.dumps(md, indent=4, sort_keys=True))
    ```

??? example "Python API client: delete metadata for a node"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    sf_client.delete_node_metadata_item(net_uuid, 'foo')
    ```