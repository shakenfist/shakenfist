# Agent Operations (/agentoperations/)

Since v0.7, when an instance is running the Shaken Fist agent, you can queue agent
operations to run on that instance. These operations consist of a series of commands
which are executed in return, with results for each being gathered as they execute.

An operation also carries timing fields: `deadline` and `progress_timeout` are the
caller's intent, and `last_progress` and `attempts` are the server's bookkeeping.
`deadline` is an absolute unix timestamp, computed when the API server received the
creating request; `progress_timeout` is a count of seconds, and reads `0` on an
operation like the one below whose commands cannot report progress. Both are set
from the optional parameters described in
[bounding how long an agent operation may take](/developer_guide/api_reference/instances/#bounding-how-long-an-agent-operation-may-take).
`last_progress` and `attempts` stay `null` and `0` until a following release begins
enforcing these values. A `null` in either caller-intent field means the operation
was created by an API server which predates them, so the server default applies
rather than "no deadline" -- see [the database operator guide](/operator_guide/database/).

In general the API for agent operations is instance-centric -- you lookup the
agent operations an instance has seen, and then can request further information
about the agent operation directly. There is currently no way to search for an
agent operation outside the context of its parent instance.

For information on how to create an agent operation for an instance, refer to
the [instances API documentation on creating agent operations](/developer_guide/api_reference/instances/#executing-commands-within-an-instance). For information on how to list the
agent operations for a given instance, refer to the [instances API documentation on listing agent operations](/developer_guide/api_reference/instances/#fetching-information-about-an-instances-agent-operations).

???+ tip "REST API calls"

    * [GET /agentoperations/{operation_uuid}](https://openapi.shakenfist.com/#/agentoperations/get_agentoperations__operation_uuid_): Lookup a specific agent operation.
    * [DELETE /agentoperations/{operation_uuid}](https://openapi.shakenfist.com/#/agentoperations/delete_agentoperations__operation_uuid_): Delete a specific agent operation.

??? example "Python API client: lookup an agent operation by uuid"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    agentop = sf_client.get_agent_operation('5a00d6f3-19b6-42bc-b1df-ddc4e5a299e9')
    print(json.dumps(agentop, indent=4, sort_keys=True))
    ```

    Which returns something like:

    ```json
    {
        "attempts": 0,
        "commands": [
            {
                "block-for-result": true,
                "command": "execute",
                "commandline": "cat /tmp/README.md"
            }
        ],
        "deadline": 1787428090.5,
        "instance_uuid": "a771fb13-aaad-4cb6-a86b-7ee51e7bacc6",
        "last_progress": null,
        "metadata": {},
        "namespace": "vdi",
        "progress_timeout": 0.0,
        "results": {
            "0": {
                "command-line": "cat /tmp/README.md",
                "result": true,
                "return-code": 0,
                "stderr": "",
                "stdout": "..."
            }
        },
        "state": "complete",
        "uuid": "5a00d6f3-19b6-42bc-b1df-ddc4e5a299e9",
        "version": 1
    }
    ```

??? example "Python API client: delete an agent operation by uuid"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    agentop = sf_client.delete_agent_operation('5a00d6f3-19b6-42bc-b1df-ddc4e5a299e9')
    ```

## Object References

Agent operation API responses include `references_to` and `references_from` fields
that show the relationships between agent operations and other objects. The
`references_from` field shows what blobs this agent operation produced (e.g.,
stdout and stderr output blobs via `agent_output` relationships).

??? example "Example references_from output for an agent operation"

    ```json
    "references_from": {
        "agent_output": [
            {
                "source_object_type": "agentoperation",
                "source_uuid": "5a00d6f3-19b6-42bc-b1df-ddc4e5a299e9",
                "relationship": "agent_output",
                "relationship_value": "stdout",
                "target_object_type": "blob",
                "target_uuid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "created": 1683995934.357137,
                "last_active": 1684054381.217045
            }
        ]
    }
    ```