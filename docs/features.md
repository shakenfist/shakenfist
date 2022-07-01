---
title: Features
---
# Feature matrix

What features does Shaken Fist have now? What about in the future? This page attempts to document the currently implemented features, but it is a bit a moving target. If you're left wondering if something works, please reach out to us and ask.

# High level functionality

Our high level functionality is why you'd consider using Shaken Fist. Specifically, we support:

* instances: which are virtual machines deployed and managed by Shaken Fist.
* virtual networks: which are VXLAN meshes between hypervisors managed by Shaken Fist. These virtual networks do automatic IP address management, optionally provide DHCP and NAT, and support floating IPs for external accessability.
* resource efficiency: we try hard to not use much in terms of resources in an orchestration idle state (that is, your workload isn't changing), but we also deploy and configure Kernel Shared Memory (KSM) and make heavy use of qcow2 Copy On Write (COW) layers to reduce the resources used by a single instance. This means you can pack more instances onto a Shaken Fist cluster than you can alternative deployments of the same size from other projects.

# Object types

We also have a lot of implementation functionality that is quite useful, but not the sort of thing you'd put on a billboard. Let's work through that by object type.

## Artifacts

Artifacts are Shaken Fist's object type for disk images -- the sort of thing that you would store in Glance in OpenStack. Artifacts can store downloaded disk images from the internet (the "image" type), snapshots of previous instances (the "snapshot" type), and arbitrary uploads (also stored with the "image" type). There is also a special "label" artifact type, which is an overlay on top of the other types. Its easiest to explain its behavior by explaining the lifecycle of an artifact.

The normal way to get your first artifact is to download something from the internet. So for example, you might start an instance with a standard Ubuntu cloud image from https://cloud-images.ubuntu.com/. This would be done by specifying the URL of the image in the disk specification of the image: Shaken Fist will then download the image and store it as an artifact, and then start your instance. A second instance using the same image will then check the image at the URL hasn't changed, and if it hasn't use the same artifact as the first instance, skipping the repeated download.

However, if the image has changed, a second version would be downloaded. Depending on the settings for the artifact, both versions are retained. By default Shaken Fist keeps the last three versions of each artifact, although this is configurable.

Now let's assume that you have a nightly CI job which starts an instance from the latest Ubuntu cloud image, and performs some tests to ensure that it works for your software stack. You want to somehow mark for your other workloads what versions of the Ubuntu image are trusted, and you do this with a label. So, your CI job would specify the upstream URL for the cloud image, perform its tests, and then label the image if it passed those tests. Other Ubuntu users in your cloud could then specify that they wanted the most recent version which passed testing by specifying the label for their disk specification, instead of the upstream URL.

Shaken Fist's CI does exactly this. Each night we download a set of cloud images, customize them to make the CI runs a bit faster (pre-installing packages and so forth), and then test that they work. At the end of that run we take a snapshot of the instance we customized, and label it with a label along the lines of "sfci-ubuntu-2004". CI jobs then use that label for their base disk. You can see the ansible we use to do this at https://github.com/shakenfist/shakenfist/blob/develop/deploy/ansible/ci-image.yml if you're interested.

The following operations are exposed on artifacts by the REST API:

| Operation | Command line client | API client |
| --- | --- | --- |
| list artifacts | `artifact list` | `get_artifacts()` |
| show an artifact | `artifact show` | `get_artifact()` |
| fetch an artifact from a URL without starting an instance | `artifact cache` | `cache_artifact()` |
| upload | `artifact upload` | `create_upload()` followed by calls to `send_upload()` and then `upload_artifact()` |
| download | `artifact download` | lookup the desired version's blob with `get_artifact()`, then download with `get_blob_data()` |
| show detailed information about versions | `artifact versions` | `get_artifact_versions()` |
| delete | `artifact delete` | `delete_artifact()` |
| delete a version | `artifact delete-version` | `delete_artifact_version()` |
| set the maximum number of versions | `artifact max-versions` | `set_artifact_max_versions()` |

Note that artifacts exist in namespaces (since v0.6). This means that your artifacts are private to your namespace, and can't be seen or used by other namespaces. There are two exceptions -- the "system" administrative namespace can see all artifacts, and the "system" namespace can create artifacts visible to all other namespaces -- this is done with the shared flag on the relevant command line or API calls, and uses a "sharedwithall" namespace in the database.

## Blobs

Each version of an artifact is an object called a blob. Blobs are stored on Shaken Fist nodes, and are automatically replicated around the cluster as required. By default we store at least two copies of each blob, although this is configurable. Its possible we'll store a lot more copies than that, because we only reap excess copies when we start to run low on disk. This is because these blobs are often used during the startup of instances, so having a local cache of popular blobs can significantly improve instance start up times.

All hypervisor nodes store blobs, but it is also possible to have "storage only" nodes which don't run VMs and just store blobs. In previous deployments we have used these storage nodes to handle having more blobs than we need for currently running instances -- for example historical snapshots we are fond of, but are unlikely to require frequent access to. The storage nodes were therefore a cheaper machine type with slower CPU and disk, but a lot more disk than our hypervisor nodes.

So for example if you had an edge deployment where you are resource constrained, but also want to take nightly instance snapshots as a backup, you might have a more centrally located storage node and Shaken Fist would migrate unused blobs there to free up space on the edge nodes as required. If a blob only present on a storage only node is required for an instance start, a hypervisor node will fetch it at that time.

Finally, blobs are reference counted. They can be used by more than one artifact (for example an image which is then labelled), and we also count how many instances are using a specific blob. We only delete a blob from disk when there are no remaining references to it.

The following operations are exposed on blobs by the REST API:

| Operation | Command line client | API client |
| --- | --- | --- |
| list blobs | `blob list` | `get_blobs()` |

## Events

Shaken Fist has an event logging system for the main object types. So for example, instead of reading through log files to find all the state changes that an instance went through, you can simply ask for a list of the events for that instance. This also means that the instance owner can see those logs without having to be given access to your log files.

The following object types currently record events: artifacts; blobs; instances; networks; networkinterfaces; nodes; and uploads. In general, events are exposed in the API as operations on the object they relate to. So for example there is a `instance events` command, which calls the `get_instance_events()` API client call. Those various calls are documented by their object type.

<!-- document common event format once it has been finalized -->

## Networks

<!-- document networks here -->

Note that networks exist in namespaces. This means that your networks are private to your namespace, and can't be seen or used by other namespaces. There is one exception -- the "system" administrative namespace can see all networks.

## Instances

Instances are the primary reason that you'd run Shaken Fist, so there's a lot to cover in their implementation. Obviously instances can be created, deleted, listed, and shown. Additionally, you can list the network interfaces on an instance, track and change metadata on a given instance (a simple key value store similar to OpenStack tags), request the current serial console output; and see events related to the instance. Instances can also have their power state managed: soft (ACPI) reboots; hard (power cycle) reboots; powered off; powered on; and paused.

When creating an instance you can configure:
* the name of the instance
* how many vCPUs the instance has
* how much memory the instance has
* what network connections the instance has, including floating IP attachments and the network interface model to use
* what disks the instance has, their size, type, and bus
* what ssh key cloud-init should set up, if your instance includes cloud-init
* other arbitrary user data which will be passed to cloud-init, if installed
* the namespace of the instance
* what video card the instance has, including the model and amount of video memory
* whether BIOS boot or UEFI boot is used
* whether secure boot is enabled, including a NVRAM template if required
* what configuration drive type is used, with a default of OpenStack style
* key and value metadata

Note that instances exist in namespaces. This means that your instances are private to your namespace, and can't be seen or used by other namespaces. There is one exception -- the "system" administrative namespace can see all instances.

# Other features

Shaken Fist supports the follow other features that are not directly related to an object type:

* JWT based API authentication
* graceful shutdown of hypervisors where current work is finished before the processes are stopped
* online upgrade of object versions as required

