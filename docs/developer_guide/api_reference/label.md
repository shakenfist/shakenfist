# Label (/label/)

Not yet documented.

??? example "Python API client: update a label"

    A common pattern is to use generic upstream Shaken Fist images as source image,
    which you then customize and test. Once testing has passed you add the tested
    version of a label which tracks "blessed" production images.

    ```python
    import json
    from shakenfist_client import apiclient
    import time

    sf_client = apiclient.Client()

    # Download a copy of Debian 11 and wait for the download to complete
    sf_client.cache_artifact('debian:11')
    a = sf_client.get_artifact('debian:11')

    while not a.get('blobs'):
        print('Waiting for first blob...')
        time.sleep(30)
        a = sf_client.get_artifact('debian:11')

    blob_index = sorted(a['blobs'].keys())[-1]
    blob_uuid = a['blobs'][blob_index]['uuid']

    print('The most recent blob UUID is %s' % blob_uuid)

    # Let's assume we've now tested this version and want to "bless" it as the
    # version we trust for production workloads. We would then add that blob as the
    # new version of our production label like this:
    prod = sf_client.update_label('debian-11-production', blob_uuid)

    print()
    print('The label artifact is now:')
    print(json.dumps(prod, indent=4, sort_keys=True))
    ```

    ```
    $ python3 example.py
    The most recent blob UUID is ffdfce7f-728e-4b76-83c2-304e252f98b1

    The label artifact is now:
    {
        "artifact_type": "label",
        "blob_uuid": "ffdfce7f-728e-4b76-83c2-304e252f98b1",
        "blobs": {
            "1": {
                "depends_on": null,
                "instances": [
                    "d512e9f5-98d6-4c36-8520-33b6fc6de15f"
                ],
                "reference_count": 2,
                "size": 403007488,
                "uuid": "ffdfce7f-728e-4b76-83c2-304e252f98b1"
            }
        },
        "index": 1,
        "max_versions": 3,
        "metadata": {},
        "namespace": "system",
        "shared": false,
        "source_url": "sf://label/system/debian-11-production",
        "state": "created",
        "uuid": "c9428ea2-a3fa-40cf-9668-61be99bb370a",
        "version": 6
    }
    ```