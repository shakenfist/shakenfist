# Artifact uploads and downloads

The general usage of artifact uploads and downloads is documented in the
[user guide](/user_guide/artifacts.md). This page documents the actual API flow
for uploading or downloading an artifact and is only useful to developers
implementing new Shaken Fist clients.

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

## Downloads

Artifact downloads are implemented as fetching the data for the desired blob. You
therefore must first lookup the versions for a given artifact and select a version
that you wish to download. You can then fetch the data for the relevant blob by
calling `/blobs/...uuid.../data` this call takes an optional query parameter of
`offset`, which specifies how many bytes into the blob to start returning data
from. This allows recommencing failed downloads.