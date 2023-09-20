# Authentication (/auth/)

## Create an API access token

Access to the REST API is granted via an access token. These tokens expire, so
you may also have to request new tokens for long lived applications from time
to time. You will receive a HTTP 401 status code if an access token has expired.

???+ note
    For further details of the authentication scheme, see the
    [developer guide](/developer_guide/authentication/).


???+ tip "REST API calls"

    * [POST /auth](https://openapi.shakenfist.com/#/auth/post_auth): Create an access token.

??? example "Python API client: creating an access token"

    The Python API client handles creating access tokens and refreshing them
    for you, so not specific action is required for this API call. The following
    code implies creation of an access token:

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    ```

??? example "curl: creating an access token"

    ```bash
    $ curl -X POST https://shakenfist/api/auth -d '{"namespace": "system", "key": "oisoSe7T"}'
    {
        "access_token": "eyJhbG...IkpXVCJ9.eyJmc...wwQ",
        "token_type": "Bearer",
        "expires_in": 900
    }
    ```

    This token is then used by passing it as a HTTP Authorization header with
    "Bearer " prepended:

    ```bash
    $ curl -X GET https://shakenfist/api/auth/namespaces \
        -H 'Authorization: Bearer eyJhbG...IkpXVCJ9.eyJmc...wwQ' \
        -H 'Content-Type: application/json'
    [
        {
            "name": "adhoc",
            "state": "created",
            "trust": {"full": ["system"]}
        }, {
            "name": "ci",
            "state": "created",
            "trust": {"full": ["system"]}
        }, {
            "name": "system",
            "state": "created",
            "trust": {"full": ["system"]}
        }
    ]
    ```

## Namespaces

Resources in a Shaken Fist cluster are divided up into logical groupings called
namespaces. All namespaces have equal permissions, except for the `system`
namespace, which is used for administrative tasks.

???+ note

    For a detailed reference on the state machine for namespaces, see the
    [developer documentation on object states](/developer_guide/state_machine/#namespaces).

???+ tip "REST API calls"

    * [GET /auth/namespaces](https://openapi.shakenfist.com/#/auth/get_auth_namespaces): List all namespaces visible to your currently authenticated namespace.
    * [POST /auth/namespaces](https://openapi.shakenfist.com/#/auth/post_auth_namespaces): Create a namespace, if you have permissions to do so.
    * [DELETE /auth/namespaces/{namespace}](https://openapi.shakenfist.com/#/auth/delete_auth_namespaces__namespace_): Delete a namespace.
    * [GET /auth/namespaces/{namespace}](https://openapi.shakenfist.com/#/auth/get_auth_namespaces__namespace_): Get details of a single namespace.

??? example "Python API client: list namespaces"

    This example lists all namespaces visible to the caller:

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    ns = sf_client.get_namespaces()
    print(json.dumps(ns, indent=4, sort_keys=True))
    ```

    Which returns something like:

    ```json
    [
        {
            "keys": [
                "jenkins"
            ],
            "metadata": {},
            "name": "ci",
            "state": "created",
            "trust": {
                "full": [
                    "system"
                ]
            },
            "version": 5
        },
        ...
    ]
    ```

??? example "Python API client: create a namespace"

    This example creates a new namespace, which is only possible if you are
    currently authenticated as the `system` namespace:

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    ns = sf_client.create_namespace('demo')
    print(json.dumps(ns, indent=4, sort_keys=True))
    ```

    Which returns something like:

    ```json
    {
        "keys": [],
        "metadata": {},
        "name": "demo",
        "state": "created",
        "trust": {
            "full": [
                "system"
            ]
        },
        "version": 5
    }
    ```

??? example "Python API client: delete a namespace"

    This example deletes a namespace, which is only possible if you are
    currently authenticated as the `system` namespace:

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    ns = sf_client.delete_namespace('demo')
    print(json.dumps(ns, indent=4, sort_keys=True))
    ```

    The call does not return anything.

??? example "Python API client: get details of a single namespace"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    ns = sf_client.get_namespace('demo')
    print(json.dumps(ns, indent=4, sort_keys=True))
    ```

    Which returns something like:

    ```json
    {
        "keys": [],
        "metadata": {},
        "name": "demo",
        "state": "created",
        "trust": {
            "full": [
                "system"
            ]
        },
        "version": 5
    }
    ```

## Namespace keys

Callers authenticate to a namespace by providing a key to a call to `/auth/` as
discussed above. The calls discussed in this section relate to the management of
the keys used to authenticate to a namespace.

???+ tip "REST API calls"

    * [GET /auth/namespaces/{namespace}/keys](https://openapi.shakenfist.com/#/auth/get_auth_namespaces__namespace__keys): List all authentication keys for a given namespace.
    * [POST /auth/namespaces/{namespace}/keys](https://openapi.shakenfist.com/#/auth/post_auth_namespaces__namespace__keys): Create a new key for a namespace.
    * [DELETE /auth/namespaces/{namespace}/keys/{key_name}](https://openapi.shakenfist.com/#/auth/delete_auth_namespaces__namespace__keys__key_name_): Delete a specific key for a namespace.
    * [PUT /auth/namespaces/{namespace}/keys/{key_name}](https://openapi.shakenfist.com/#/auth/put_auth_namespaces__namespace__keys__key_name_): Update a key for a namespace.

??? example "Python API client: list all keys for a namespace"

    This example lists all the keys in a namespace:

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    keys = sf_client.get_namespace_keynames('ci')
    print(keys)
    ```

    Which returns something like:

    ```json
    ['jenkins']
    ```

??? example "Python API client: create a new key for a namespace"

    This example adds a key to a namespace and then lists all keys:

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    sf_client.add_namespace_key('ci', 'newkey', 'thesecretvalue')

    # Fetch the list of keys to make sure the new one exists
    keys = sf_client.get_namespace_keynames('ci')
    print(keys)
    ```

    Which returns something like:

    ```json
    ['jenkins', 'newkey']
    ```

??? example "Python API client: remove a specific key from a namespace"

    This example deletes a key from the namespace and then lists all keys:

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    sf_client.delete_namespace_key('ci', 'newkey')

    # Fetch the list of keys to make sure the new one exists
    keys = sf_client.get_namespace_keynames('ci')
    print(keys)
    ```

    Which returns something like:

    ```json
    ['jenkins']
    ```

??? example "Python API client: update the secret portion of an existing namespace key"

    This example updates the secret portion of an existing namespace key to a new value:

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    sf_client.update_namespace_key('ci', 'newkey', 'newsecret')
    ```

## Metadata

All objects exposed by the REST API may have metadata associated with them. This
metadata is for storing values that are of interest to the owner of the resources,
not Shaken Fist. Shaken Fist does not attempt to interpret these values at all,
with the exception of the [instance affinity metadata values](/user_guide/affinity/).
The metadata store is in the form of a key value store, and a general introduction
is available [in the user guide](/user_guide/metadata/).

???+ tip "REST API calls"

    * [GET ​/namespaces/{namespace}​/metadata](https://openapi.shakenfist.com/#/auth/get_auth_namespaces__namespace__metadata): Get metadata for a namespace.
    * [POST /namespaces/{namespace}/metadata](https://openapi.shakenfist.com/#/auth/post_auth_namespaces__namespace__metadata): Create a new metadata key for a namespace.
    * [DELETE /namespaces/{namespace}/metadata/{key}](https://openapi.shakenfist.com/#/auth/delete_auth_namespaces__namespace__metadata__key_): Delete a specific metadata key for a namespace.
    * [PUT /namespaces/{namespace}/metadata/{key}](https://openapi.shakenfist.com/#/auth/delete_auth_namespaces__namespace__trust__external_namespace_): Update an existing metadata key for a namespace.

??? example "Python API client: set metadata on a namespace"

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    sf_client.set_artifact_metadata_item(img_uuid, 'foo', 'bar')
    ```

??? example "Python API client: get metadata for a namespace"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    md = sf_client.get_artifact_metadata(img_uuid)
    print(json.dumps(md, indent=4, sort_keys=True))
    ```

??? example "Python API client: delete metadata for a namespace"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    sf_client.delete_artifact_metadata_item(img_uuid, 'foo')
    ```