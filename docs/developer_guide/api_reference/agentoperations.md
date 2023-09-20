# Agent Operations (/agentoperations/)

Since v0.7, when an instance is running the Shaken Fist agent, you can queue agent
operations to run on that instance. These operations consist of a series of commands
which are executed in return, with results for each being gathered as they execute.
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
        "commands": [
            {
                "block-for-result": true,
                "command": "execute",
                "commandline": "cat /tmp/README.md"
            }
        ],
        "instance_uuid": "a771fb13-aaad-4cb6-a86b-7ee51e7bacc6",
        "metadata": {},
        "namespace": "vdi",
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