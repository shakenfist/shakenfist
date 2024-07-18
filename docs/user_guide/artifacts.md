# Artifacts

Shaken Fist uses artifacts as disk templates for new instances. You therefore
need to have a least one artifact before you can create your first instance,
although there is a shorthand notation to create that artifact during instance
creation.

The following artifact types exist:

* *images*: these are generally downloaded from the Internet, although they can
  also be created via an upload mechanism (see the artifact uploads section below
  for a detailed introduction to uploading images to the cluster).
* *snapshots*: these are created by taking a copy of the disk(s) of a running
  instance.
* *labels*: labels are a bit like symbolic links, although they still have
  versioning like other forms of artifact.
* *other*: a catch all for artifacts which don't fall into any of the other
  categories. For example captured instance console output archived after an
  instance was deleted.

Behind the scenes, artifacts are references to blobs. You can think of them as
symlinks if you'd like. All types of artifact support versioning. This is implemented
by having each artifact have a list of blobs. You can see this in the output of a
`sf-client artifact show ...` command:

```
$ sf-client artifact show 3420f4ac-529a-4b34-b8d8-c05a838b9e0c
uuid                     : 3420f4ac-529a-4b34-b8d8-c05a838b9e0c
namespace                : ci-images
type                     : label
state                    : created
source url               : sf://label/ci-images/debian-11
current version blob uuid: cc6a6a96-8182-474a-ab31-45f1f9310b44
number of versions       : 3
maximum versions         : 3
shared                   : False

Versions:
    4  : blob f6090574-321c-4dec-b381-0caf001eeba5 is 2964.1MB
    5  : blob 77b9032a-0d3e-4cc2-bb73-1730ad3c3cb0 is 2960.1MB
    6  : blob cc6a6a96-8182-474a-ab31-45f1f9310b44 is 2950.4MB in use by
         instances 78d566f1-c664-46d9-aa46-cf717aa63743
```

Here we can see a few things:

* The artifact is a label in the ci-images namespace.
* There is a source URL, which is how you would reference this artifact when
  starting an instance.
* There are three versions of the artifact currently stored (`number of versions`),
  which is the maximum (`maximum versions`). However, there have been six versions
  of this artifact ever (as shown by the indices of the versions being 4, 5, and
  6).
* The most recent version is currently in use by an instance.

## Creating an image artifact

Image artifacts are created by specifying the URL of an image to download. This
can be either in the form of an explicit request to cache a URL with a
`sf-client artifact cache` command, or implied by specifying the URL of the
image in the specification for an instance.

There is also a shorthand form of image URLs where you are using an image from
https://images.shakenfist.com -- in that case you can use urls like `debian:11`
so specify the latest version of a Debian 11 base image. The valid shorthands
are those listed in the top level directory listing of that site. At the time of
writing this is:

* centos (shorthand for centos:8-stream)
* centos:7
* centos:8-stream
* centos:9-stream
* debian (shorthand for debian:12)
* debian:10
* debian:11
* debian:12
* debian-docker:11 (debian 11 with docker pre-installed)
* debian-docker:12 (debian 12 with docker pre-installed)
* debian-gnome:11 (debian 11 with the gnome desktop pre-installed)
* debian-gnome:12 (debian 12 with the gnome desktop pre-installed)
* debian-xfce:11 (debian 11 with the xfce desktop pre-installed)
* debian-xfce:12 (debian 12 with the xfce desktop pre-installed)
* fedora (shorthand for fedora:40)
* fedora:34
* fedora:38
* fedora:39
* fedora:40
* ubuntu (shorthand for ubuntu:24.04)
* ubuntu:18.04
* ubuntu:20.04
* ubuntu:22.04
* ubnutu:24.04

These images are updated nightly by an automated job from https://github.com/shakenfist/images.

Whenever you specify a URL for an image (either a new `cache` command or at
instance start), the URL is checked. If the image has changed a new version is
downloaded, otherwise the already cached version is used.

You can also create an image artifact by uploading it, but that's complicated
enough that its covered separately in [the developer guide section on artifacts](/developer_guide/api_reference/artifacts/).

## Creating a snapshot artifact

These are created by the `sf-client instance snapshot` command. You can specify
which disk to snapshot on a multi-disk instance with the `--device` flag. Repeated
snapshots of the same instance will result in multiple versions of the one
artifact being created. Note that there is an artifact per device snapshotted, so
a single snapshot of a multi-disk instance will create multiple artifacts.

## Creating label artifacts

So what's a label? Well downloading new versions of images automatically is great,
but what if I want to ensure the version from two weeks ago that I tested is the
one I use? Or what if I want to refer to my favourite instance snapshot by
something more convenient than a snapshot URL like
`sf://instance/78d566f1-c664-46d9-aa46-cf717aa63743/vda`? Well, labels are the
answer to those questions.

Labels are artifacts where _you_ specify what the new version should be. So in the
download example you'd test an image version and when you decide that its right,
you'd add that version's blob UUID to your label of known tested versions.

Blobs are reference counted, so even if the image artifact ages out a version,
having that version referred to in a label artifact protects it from deletion.

An example of labelling a known good version of an artifact would be something
like this:

```
$ sf-client artifact show 3420f4ac-529a-4b34-b8d8-c05a838b9e0c
uuid                     : 3420f4ac-529a-4b34-b8d8-c05a838b9e0c
namespace                : ci-images
type                     : label
state                    : created
source url               : sf://label/ci-images/debian-11
current version blob uuid: cc6a6a96-8182-474a-ab31-45f1f9310b44
number of versions       : 3
maximum versions         : 3
shared                   : False

Versions:
    4  : blob f6090574-321c-4dec-b381-0caf001eeba5 is 2964.1MB
    5  : blob 77b9032a-0d3e-4cc2-bb73-1730ad3c3cb0 is 2960.1MB
    6  : blob cc6a6a96-8182-474a-ab31-45f1f9310b44 is 2950.4MB in use by
         instances 78d566f1-c664-46d9-aa46-cf717aa63743

...test version 6 with blob UUID cc6a6a96-8182-474a-ab31-45f1f9310b44...

$ sf-client label update my-tested-thing cc6a6a96-8182-474a-ab31-45f1f9310b44
```

If the label `my-tested-thing` does not exist, it will be created the first
time you update it.

## Listing and deleting artifacts

Artifacts follow the same user interface patterns as other objects. That is, you
can list artifacts with this command:

`sf-client artifact list`

And you can delete artifacts with a command like this:

`sf-client artifact delete ...name.or.uuid...`

Note that deleting an artifact does not necessarily imply deleting the associated
blobs. If those blobs are in use by other objects (artifacts, instances, and
so on) then they remain stored by the cluster until there are no remaining
references.

Additionally, you can also delete _all_ artifacts in a given namespace by making
a HTTP DELETE request to /artifacts REST API endpoint, which is also provided by
the `delete_all_artifacts()` method in the Python API client. This functionality
is not currently exposed in the command line client.

Finally, deleting a namespace implies deleting all artifacts within that namespace,
so show care when deleting namespaces to ensure they no longer contain any data
you are fond of.

## Controlling the number of versions

You can also control the number of versions stored by an artifact with the
`sf-client artifact max-versions` command.

## Blob replication

You can control the number of copies of a given blob are stored in the cluster
as well. This protects against machine or disk failures causing data loss. The
default number of replicas is 2, but this is not configurable per-blob. It is
configured with the `BLOB_REPLICATION_FACTOR` configuration variable.

## Artifact uploads and downloads

Artifacts may also be uploaded and downloaded. This means you can extract a
snapshot from your cluster for offline backup (or movement to another cloud),
or upload an image built with a tool like Hashicorp Packer.

To upload an artifact, use the `sf-client artifact upload` command. To download
an artifact, use the `sf-client artifact download` command.

Shaken Fist will calculate a checksum for the new blob created by an upload, and
if it already has a blob matching that checksum it will only store the data once.
This makes uploading a given artifact more than once effectively free apart from
a small amount of etcd storage.