# Blobs (/blobs/)

In general you interact with blobs as attributes of other objects -- blobs
being the most obvious example. However, there is limited support in the REST API
for interacting directly with blobs as well. Blobs are also considered a special
case in that they are not namespaced like most other objects. That is, possession
of the UUID of a blob is considered evidence that you should have access to it.
This is because blobs can be shared between objects if the data for those
objects is currently the identical. This is also why only administrators can list
all blobs in a given Shaken Fist cluster.

???+ note

    For a detailed reference on the state machine for blobs, see the
    [developer documentation on object states](/developer_guide/state_machine/#blobs).

## Fetching information about a blob

If you know the UUID of a blob, then you can GET information about that blob from
the REST API, as well as fetching the actual data the blob is storing as well.

???+ tip "REST API calls"

    * [GET /blobs](https://openapi.shakenfist.com/#/blobs/get_blobs): List all blobs in a cluster. You must be an administrator to make this call.
    * [GET /blobs/{blob_uuid}](https://openapi.shakenfist.com/#/blobs/get_blobs__blob_uuid_): Get information about a specific blob.
    * [GET /blobs/{blob_uuid}/data](https://openapi.shakenfist.com/#/blobs/get_blobs__blob_uuid__data): Get the stored data for a specific blob.
    * [GET /blob_checksums/sha512/{hash}](https://openapi.shakenfist.com/#/blobs/get_blob_checksums_sha512__hash_): Find a blob with this hash.

??? example "Python API client: get a specific blob"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    b = sf_client.get_blob('578da8b6-eb98-4e10-bb36-e4d4d763d312')
    print(json.dumps(b, indent=4, sort_keys=True))
    ```

??? example "Python API client: download the data for a specific blob"

    This example requires retry logic to handle the HTTP connection dropping
    while transferring large files.

    ```python
    import http
    from shakenfist_client import apiclient
    import requests
    import sys
    import urllib3

    sf_client = apiclient.Client()

    total = 0
    connection_failures = 0
    done = False

    with open('output', 'wb') as f:
        while not done:
            bytes_in_attempt = 0

            try:
                for chunk in sf_client.get_blob_data(
                        '578da8b6-eb98-4e10-bb36-e4d4d763d312', offset=total):
                    received = len(chunk)
                    f.write(chunk)
                    bytes_in_attempt += received
                    total += received

                done = True

            except urllib3.exceptions.NewConnectionError as e:
                connection_failures += 1
                if connection_failures > 2:
                    print('HTTP connection repeatedly failed: %s' % e)
                    sys.exit(1)

            except (ConnectionResetError, http.client.IncompleteRead,
                    urllib3.exceptions.ProtocolError,
                    requests.exceptions.ChunkedEncodingError) as e:
                # An API error (or timeout) occurred. Retry unless we got nothing.
                if bytes_in_attempt == 0:
                    print('HTTP connection dropped without transferring data: %s' % e)
                    sys.exit(1)
    ```

??? example "Python API client: search for a blob by sha512 hash"

    Note that this call is not supported by older versions of the Shaken Fist
    REST API.

    ```python
    import hashlib
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    if not sf_client.check_capability('blob-search-by-hash'):
        print('Searching by hash is not supported')
    else:
        sha512_hash = hashlib.sha512()
        with open('input', 'rb') as f:
            d = f.read(4096)
            while d:
                sha512_hash.update(d)
                d = f.read(4096)

        print('Searching for a pre-existing blob with this hash...')
        b = sf_client.get_blob_by_sha512(sha512_hash.hexdigest())
        if not b:
            print('No matching blob found')
        else:
            print('Blob %s is a match' % b['uuid'])
    ```

## Object References

Blob API responses include `references_to` and `references_from` fields that
show the relationships between blobs and other objects in the system. These
fields help you understand how blobs are connected to instances, artifacts,
and other blobs.

The `references_to` field shows what objects reference this blob (e.g., which
instances use this blob as a disk). The `references_from` field shows what
objects this blob references (e.g., dependency blobs or transcoded versions).

Each reference includes:

* `source_object_type`: The type of the object that holds the reference
* `source_uuid`: The UUID of the source object
* `relationship`: The type of relationship (disk, artifact_index, depends_on, transcode, blob_location)
* `relationship_value`: Optional additional context (e.g., disk index "0", transcode style "qcow2")
* `target_object_type`: The type of the referenced object
* `target_uuid`: The UUID of the referenced object
* `created`: Unix timestamp when the reference was created
* `last_active`: Unix timestamp when the reference was last observed to be valid

??? example "Example references_to output for a blob"

    ```json
    "references_to": {
        "disk": [
            {
                "source_object_type": "instance",
                "source_uuid": "0a56ef2c-8331-4ed7-a443-267f53bfb24c",
                "relationship": "disk",
                "relationship_value": "0",
                "target_object_type": "blob",
                "target_uuid": "578da8b6-eb98-4e10-bb36-e4d4d763d312",
                "created": 1683995934.357137,
                "last_active": 1684054381.217045
            }
        ],
        "artifact_index": [
            {
                "source_object_type": "artifact",
                "source_uuid": "69ff59a7-f6ac-4f64-a575-bb54a7ee8961",
                "relationship": "artifact_index",
                "relationship_value": "000000000101",
                "target_object_type": "blob",
                "target_uuid": "578da8b6-eb98-4e10-bb36-e4d4d763d312",
                "created": 1683995934.357137,
                "last_active": 1684054381.217045
            }
        ]
    }
    ```

## Metadata

All objects exposed by the REST API may have metadata associated with them. This
metadata is for storing values that are of interest to the owner of the resources,
not Shaken Fist. Shaken Fist does not attempt to interpret these values at all,
with the exception of the [instance affinity metadata values](/user_guide/affinity/).
The metadata store is in the form of a key value store, and a general introduction
is available [in the user guide](/user_guide/metadata/).

???+ tip "REST API calls"

    * [GET ​/blobs​/{blob_uuid}​/metadata](https://openapi.shakenfist.com/#/blobs/get_blobs__blob_uuid__metadata): Get metadata for a blob.
    * [POST /blobs/{blob_uuid}/metadata](https://openapi.shakenfist.com/#/blobs/post_blobs__blob_uuid__metadata): Create a new metadata key for a blob.
    * [DELETE /blobs/{blob_uuid}/metadata/{key}](https://openapi.shakenfist.com/#/blobs/delete_blobs__blob_uuid__metadata__key_): Delete a specific metadata key for a blob.
    * [PUT /blobs/{blob_uuid}/metadata/{key}](https://openapi.shakenfist.com/#/blobs/put_blobs__blob_uuid__metadata__key_): Update an existing metadata key for a blob.

??? example "Python API client: set metadata on a blob"

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    sf_client.set_blob_metadata_item(blob_uuid, 'foo', 'bar')
    ```

??? example "Python API client: get metadata for a blob"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    md = sf_client.get_blob_metadata(blob_uuid)
    print(json.dumps(md, indent=4, sort_keys=True))
    ```

??? example "Python API client: delete metadata for a blob"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    sf_client.delete_blob_metadata_item(blob_uuid, 'foo')
    ```