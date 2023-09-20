# Artifacts (/artifacts/)

The general usage of artifacts is documented in the [user guide](/user_guide/artifacts/). This page documents the API flow interacting with artifacts, as well as the
multi-step process involved with uploading or downloading an artifact.

???+ note
    For a detailed reference on the state machine for artifacts, see the
    [developer documentation on object states](/developer_guide/state_machine/#artifacts).

## Fetching a remote URL as an image artifact

As discussed in the [user guide](/user_guide/artifacts/), remote URLs can be
stored within a Shaken Fist cluster as artifacts of type *image*. These artifacts
are often used as the template for disks attached to instances and are the
equivalent to AWS AMIs or OpenStack images in Glance.

???+ tip "REST API calls"

    * [POST /artifacts](https://openapi.shakenfist.com/#/artifacts/post_artifacts): Fetch an image artifact into the cluster.

??? example "Python API client: cache an artifact"

    Note this API call is asynchronous and therefore returns immediately. Use
    artifact version information and events to determine if a new version was
    fetched and if that fetch is complete.

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    sf_client.cache_artifact('https://images.shakenfist.com/debian:11/latest.qcow2')
    ```

## Fetching information about an artifact

As expected, you can use a GET REST API call to fetch information about all
artifacts in a namespace, or a specific artifact for more detailed information.
Artifacts also track "events" (see the [user guide](/user_guide/events/) for
a general introduction to the Shaken Fist event system).

???+ tip "REST API calls"

    * [GET /artifacts](https://openapi.shakenfist.com/#/artifacts/get_artifacts): List artifacts for a namespace.
    * [GET /artifacts/{artifact_ref}](https://openapi.shakenfist.com/#/artifacts/get_artifacts__artifact_ref_): Get information about a specific artifact.
    * [GET /artifacts/{artifact_ref}/events](https://openapi.shakenfist.com/#/artifacts/get_artifacts__artifact_ref__events): Fetch events for a specific artifact.

??? example "Python API client: list artifacts"

    Note this API call is asynchronous and therefore returns immediately. Use
    artifact version information and events to determine if a new version was
    fetched and if that fetch is complete.

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    artifacts = sf_client.get_artifacts()

    print('uuid,namespace,type,source_url,versions,state,shared')
    for meta in artifacts:
        versions = '%d of %d' % (len(meta.get('blobs', [])),
                                 meta.get('index', 'unknown'))
        print('%s,%s,%s,%s,%s,%s,%s' % (
              meta.get('uuid', ''), meta.get('namespace', ''),
              meta.get('artifact_type', ''),
              meta.get('source_url', ''), versions,
              meta.get('state', ''), meta.get('shared', False)))
    ```

    ```bash
    $ python3 example.py
    uuid,namespace,type,source_url,versions,state,shared
    05e841a7-7e13-4df6-8c04-8932b98885bd,system,image,sf://upload/system/centos-9-stream,1 of 1,created,False
    1646bc22-674b-4a46-97e5-f767a6e82d1c,system,image,debian:11,3 of 9,created,False
    ...
    ```

??? example "Python API client: get information about a specific artifact"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    artifact = sf_client.get_artifact('05e841a7-7e13-4df6-8c04-8932b98885bd')
    print(json.dumps(artifact, indent=4, sort_keys=True))
    ```

    ```json
    {
        "artifact_type": "image",
        "blob_uuid": "8fa321aa-1e05-43c9-ade1-36d45940d6bd",
        "blobs": {
            "1": {
                "depends_on": null,
                "instances": [],
                "reference_count": 2,
                "size": 960546304,
                "uuid": "8fa321aa-1e05-43c9-ade1-36d45940d6bd"
            }
        },
        "index": 1,
        "max_versions": 3,
        "metadata": {},
        "namespace": "system",
        "shared": false,
        "source_url": "sf://upload/system/centos-9-stream",
        "state": "created",
        "uuid": "05e841a7-7e13-4df6-8c04-8932b98885bd",
        "version": 6
    }
    ```

??? example "Python API client: list events for an artifact"

    ``` python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    events = sf_client.get_artifact_events('05e841a7-7e13-4df6-8c04-8932b98885bd')
    print(json.dumps(events, indent=4, sort_keys=True))
    ```

    Note that events are returned in reverse chronological order and are limited
    to the 100 most recent events.

    ```json
    [
        ...
        {
            "duration": null,
            "extra": {},
            "fqdn": "sf-3",
            "message": "artifact fetch complete",
            "timestamp": 1684718452.2673004,
            "type": "audit"
        },
        ...
    ]
    ```

## Deleting artifacts

Artifacts may be deleted individually, or an entire namespace at a time.

???+ tip "REST API calls"

    * [DELETE /artifacts](https://openapi.shakenfist.com/#/artifacts/delete_artifacts): Delete all artifacts within a specific namespace.
    * [DELETE /artifacts/{artifact_ref}](https://openapi.shakenfist.com/#/artifacts/delete_artifacts__artifact_ref_): Delete a specific artifact.

??? example "Python API client: delete a single artifact"

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    sf_client.delete_artifact('05e841a7-7e13-4df6-8c04-8932b98885bd')
    ```

??? example "Python API client: delete all artifacts in a namespace"

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    sf_client.delete_all_artifacts('mynamespace')
    ```

## Uploads

Artifact uploads normally require multiple HTTP requests in order to complete.
This is because artifacts are often very large, and the REST API wants to allow
you to continue an upload even if a single HTTP session fails or times out. This
is implemented by creating an upload object, POSTing data to that object repeatedly,
and then converting that upload object to an artifact.

*Upload objects which have not have data posted to them in a long time (currently
24 hours) are automatically removed.*

You create an upload by POST'ing to `/upload`. This will create a new upload
object and return you a JSON representation of that object. The JSON includes
the UUID, node the upload is stored on, and when it was created.

Then repeatedly POST binary data to `/upload/...uuid...`. This binary data is
blindly appended to your upload object. Do not encode the data with base64 or
similar. Each call will return the new size of the object.

If necessary, you can also truncate an upload object to a specified size, for
example if you are unsure that a POST operation completed correctly. You do this
by sending a POST to `/upload/...uuid.../truncate/...desired.length...`.

Once your upload is complete, you convert it to an artifact by calling
`/artifacts/upload/...name...` to convert it to an artifact.

There is one final optimization to uploads, which is implemented in the python
API and command line clients. If before upload you calculate a sha512 of the
object to be uploaded, you can then search for that checksum with the
`/blob_checksums/sha512/...hash...` endpoint. If a blob is returned then you
don't need to actually upload and can instead pass that blob uuid (with a POST
argument named `blob_uuid`) instead of an upload uuid to the
`/artifacts/upload/...name...` endpoint. See the swagger documentation for more
details.

???+ note

    For a detailed reference on the state machine for uploads, see the
    [developer documentation on object states](/developer_guide/state_machine/#uploads).

???+ tip "REST API calls"

    * [POST /upload](https://openapi.shakenfist.com/#/upload/post_upload): Create a new upload.
    * [POST /upload/{upload_uuid}](https://openapi.shakenfist.com/#/upload/post_upload__upload_uuid_): Append a new chunk to an already existing upload.
    * [POST /upload/{upload_uuid}/truncate](https://openapi.shakenfist.com/#/upload/post_upload__upload_uuid__truncate__offset_): Truncate an upload to a specific index. This can be useful as a retry operation in the case of a transmission error.
    * [GET /artifacts](https://openapi.shakenfist.com/#/artifacts/get_artifacts): List artifacts.
    * [POST /artifacts/upload/{artifact_name}](https://openapi.shakenfist.com/#/artifacts/post_artifacts_upload__artifact_name_): Convert a completed upload into an artifact.

??? example "Python API client: create an upload, transfer data, and convert to an artifact"

    ```python
    from shakenfist_client import apiclient
    import time

    sf_client = apiclient.Client()
    upload = sf_client.create_upload()

    buffer_size = 4096
    total = 0
    retries = 0
    with open('/tmp/input', 'rb') as f:
        d = f.read(buffer_size)
        while d:
            start_time = time.time()
            try:
                remote_total = sf_client.send_upload(upload['uuid'], d)
                retries = 0
            except apiclient.APIException as e:
                retries += 1

                if retries > 5:
                    print('Repeated failures, aborting')
                    raise e

                print('Upload error, retrying...')
                sf_client.truncate_upload(upload['uuid'], total)
                f.seek(total)
                buffer_size = 4096
                d = f.read(buffer_size)
                continue

            # We aim for each chunk to take three seconds to transfer. This is
            # partially because of the API timeout on the other end, but also
            # so that uploads don't appear to stall over very slow networks.
            # However, the buffer size must also always be between 4kb and 4mb.
            elapsed = time.time() - start_time
            buffer_size = int(buffer_size * 3.0 / elapsed)
            buffer_size = max(4 * 1024, buffer_size)
            buffer_size = min(2 * 1024 * 1024, buffer_size)

            sent = len(d)
            total += sent

            if total != remote_total:
                print('Remote side has %d, we have sent %d!'
                      % (remote_total, total))
                sys.exit(1)

            d = f.read(buffer_size)

        print('Creating artifact')
        artifact = sf_client.upload_artifact('example', upload['uuid'])
        print('Created artifact %s' % artifact['uuid'])
    ```

    ```bash
    $ python3 example.py
    Creating artifact
    Created artifact 2d9c1d4c-3436-4ea8-9b60-833fe791eece
    $ sf-client artifact show 2d9c1d4c-3436-4ea8-9b60-833fe791eece
    uuid                     : 2d9c1d4c-3436-4ea8-9b60-833fe791eece
    namespace                : system
    type                     : image
    state                    : created
    source url               : sf://upload/system/example
    current version blob uuid: 36e846f6-ae20-46e9-8377-1c123f22b610
    number of versions       : 1
    maximum versions         : 3
    shared                   : False

    Versions:
        1  : blob 36e846f6-ae20-46e9-8377-1c123f22b610 is 1.3MB
    $ sf-client artifact delete 2d9c1d4c-3436-4ea8-9b60-833fe791eece
    ```

## Using an already existing blob as a new version of an artifact

*Label* artifacts are effectively a sequence of already existing blobs which have
been categorized in some manner. For example, you might have a CI system producing
golden images for a system, but then use a label artifact to indicate the most
recent version of the golden image which has passed acceptance testing. You
therefore can apply an existing blob (the version of the golden image you tested in
our example), to an artifact as a new version.

## Removing a specific version from an artifact

It is also possible that a bad version of an artifact exists -- possibly because
the system that creates the new versions experienced an error. You can therefore
remove a specific version of an artifact as well.

???+ tip "REST API calls"

    * [DELETE /artifacts/{artifact_ref}/versions/{version_id}](https://openapi.shakenfist.com/#/artifacts/delete_artifacts__artifact_ref__versions__version_id_): Remove a specified version from an artifact.

??? example "Python API client: delete a specific version of an artifact"

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    sf_client.delete_artifact_version('2d9c1d4c-3436-4ea8-9b60-833fe791eece', 1)
    ```

## Setting the maximum number of versions of an artifact

You can configure the number of versions a given artifact will store. The default
value is the ARTIFACT_MAX_VERSIONS_DEFAULT configuration variable, but that may be
overridden on a per-artifact basis.

???+ tip "REST API calls"

    * [POST /artifacts/{artifact_ref}/versions](https://openapi.shakenfist.com/#/artifacts/get_artifacts__artifact_ref__versions): Set the maximum number of versions of an artifact to store.

??? example "Python API client: delete a specific version of an artifact"

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    sf_client.set_artifact_max_versions('2d9c1d4c-3436-4ea8-9b60-833fe791eece', 7)
    ```

## Downloads

Artifact downloads are implemented as fetching the data for the desired blob. You
therefore must first lookup the versions for a given artifact and select a version
that you wish to download. You can then fetch the data for the relevant blob by
calling `/blobs/...uuid.../data` this call takes an optional query parameter of
`offset`, which specifies how many bytes into the blob to start returning data
from. This allows recommencing failed downloads.

???+ tip "REST API calls"

    * [GET /artifacts/{artifact_ref}/versions](https://openapi.shakenfist.com/#/artifacts/get_artifacts__artifact_ref__versions): List version blobs for a given artifact.

??? example "Python API client: list versions of an artifact"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    vers = sf_client.get_artifact_versions('2d9c1d4c-3436-4ea8-9b60-833fe791eece')
    print(json.dumps(vers, indent=4, sort_keys=True))
    ```

## Sharing and unsharing

As described in the [operator guide](/operator_guide/artifacts/), artifacts in
the system namespace can be shared with all other namespaces. This is desirable
for commonly used "official" images which many users will want to use.

???+ tip "REST API calls"

    * [POST /artifacts/{artifact_ref}/share](https://openapi.shakenfist.com/#/artifacts/post_artifacts__artifact_ref__share): Share an artifact.
    * [POST /artifacts/{artifact_ref}/unshare](https://openapi.shakenfist.com/#/artifacts/post_artifacts__artifact_ref__unshare): Unshare an artifact.

??? example "Python API client: share an artifact"

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    sf_client.share_artifact('2d9c1d4c-3436-4ea8-9b60-833fe791eece')
    ```

??? example "Python API client: unshare an artifact"

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    sf_client.unshare_artifact('2d9c1d4c-3436-4ea8-9b60-833fe791eece')
    ```

## Metadata

All objects exposed by the REST API may have metadata associated with them. This
metadata is for storing values that are of interest to the owner of the resources,
not Shaken Fist. Shaken Fist does not attempt to interpret these values at all,
with the exception of the [instance affinity metadata values](/user_guide/affinity/).
The metadata store is in the form of a key value store, and a general introduction
is available [in the user guide](/user_guide/metadata/).

???+ tip "REST API calls"

    * [GET ​/artifacts​/{artifact_ref}​/metadata](https://openapi.shakenfist.com/#/artifacts/get_artifacts__artifact_ref__metadata): Get metadata for an artifact.
    * [POST /artifacts/{artifact_ref}/metadata](https://openapi.shakenfist.com/#/artifacts/post_artifacts__artifact_ref__metadata): Create a new metadata key for an artifact.
    * [DELETE /artifacts/{artifact_ref}/metadata/{key}](https://openapi.shakenfist.com/#/artifacts/delete_artifacts__artifact_ref__metadata__key_): Delete a specific metadata key for an artifact.
    * [PUT /artifacts/{artifact_ref}/metadata/{key}](https://openapi.shakenfist.com/#/artifacts/put_artifacts__artifact_ref__metadata__key_): Update an existing metadata key for an artifact.

??? example "Python API client: set metadata on an artifact"

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    sf_client.set_artifact_metadata_item(img_uuid, 'foo', 'bar')
    ```

??? example "Python API client: get metadata for an artifact"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    md = sf_client.get_artifact_metadata(img_uuid)
    print(json.dumps(md, indent=4, sort_keys=True))
    ```

??? example "Python API client: delete metadata for an artifact"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    sf_client.delete_artifact_metadata_item(img_uuid, 'foo')
    ```