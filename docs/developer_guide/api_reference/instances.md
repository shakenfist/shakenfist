# Instances (/instances/)

Instances sit at the core of Shaken Fist's functionality, and are the component
which ties most of the other concepts in the API together. Therefore, they are
also the most complicated part of Shaken Fist to explain.

## Fetching information about an instance

There are two main ways to fetch information about instances -- you can list all
instances visible to your authenticated namespace, or you can collect information
about a specific instance by providing its UUID or name.

???+ info

    Note that the amount of information visible in an instance response will change
    over the lifecycle of the instance -- for example when you first request the
    instance be created versus when the instance has had its disk specification
    calculated.

???+ tip "REST API calls"

    * [GET /instances](https://sfcbr.shakenfist.com/api/apidocs/#/instances/get_instances): List all instances visible to your authenticated namespace.
    * [GET /instances/{instance_ref}](https://sfcbr.shakenfist.com/api/apidocs/#/instances/get_instances__instance_ref_): Get information about a specific instance.

??? example "Python API client: get all visible instances"

    ``` python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    instances = sf_client.get_instances()
    print(json.dumps(instances, indent=4, sort_keys=True))
    ```

??? example "Python API client: get information about a specific instance"

    ``` python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    i = sf_client.get_instance('317e9b70-8e26-46af-a1c4-76931c0da5a9')
    print(json.dumps(i, indent=4, sort_keys=True))
    ```

## Instance lifecycle

Instances may be created, deleted, ...other stuff you'll enjoy...

???+ info

    Instance creation is by far the most complicated call in Shaken Fist in terms
    of the arguments that it takes. The code in the Python command line client is
    helpful if you need a fully worked example of every possible permutation. The
    OpenAPI documentation at XXX provides comprehensive and up to date documentation
    on all the arguments to the creation call.

???+ tip "REST API calls"

    * [DELETE /instances/{instance_ref}](https://sfcbr.shakenfist.com/api/apidocs/#/instances/delete_instances__instance_ref_): Delete an instance.

??? example "Python API client: create and then delete a simple instance"

    ``` python
    from shakenfist_client import apiclient
    import time

    sf_client = apiclient.Client()
    i = sf_client.create_instance(
        'example', 1, 1024, None,
        [{
            'size': 20,
            'base': 'debian:11',
            'bus': None,
            'type': 'disk'
        }],
        None, None)

    time.sleep(30)

    i = sf_client.delete_instance(i['uuid'])
    ```