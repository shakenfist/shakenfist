# Authentication

???+ info

    For a detailed discussion of how Shaken Fist authentication works, please see
    the discussion in the [developer guide](/developer_guide/authentication/).

## Trusts

???+ info

    Trusts are a newer way of sharing between namespaces with granular control.
    If you instead are interested in making artifacts available to all users of
    a Shaken Fist cluster, then you should also consider artifact sharing, which
    is discussed in the [artifacts section of the operators guide](artifacts.md).

The system namespace is special in a Shaken Fist cluster in that it can see
objects in all other namespaces. That is, if you are authenticated as the system
namespace and list instances, you get not only the instances in the system
namespace, but also all those in other namespaces. The same is true for other
namespaced objects such as networks and artifacts.

In older versions of Shaken Fist this behavior was hard coded, but as of
Shaken Fist v0.7 this is now implemented more flexibly. The system namespace
must still be able to see every other namespace, but you can also create a
"trust" relationship between two arbitrary namespaces to achieve the same result
on a smaller scale. In fact, the system namespace is now simply a default trust
that all other namespaces have a relationship with.

The Shaken Fist CI system uses these trusts for base images for CI runs. Each
night we rebuild a series of base test images -- Debian 10, Debian 11, Ubuntu
20.04 and so on. Each Shaken Fist CI job is run in its own namespace, so we needed
a place to store these base images, as well as a mechanism for other CI jobs to
be able to see them.

What we implemented was:

* a namespace to store the base images (we called it `ci-images`).
* when our CI conductor creates a new CI runner and associated namespace, it
  creates a trust between that ephemeral namespace and the `ci-images` namespace.
* jobs to create new images build them in their local namespace, and then "gift"
  them to the `ci-images` namespace via a label.
* jobs which need to boot a test image can now see the images from the `ci-images`
  namespace by virtue of this trust relationship.