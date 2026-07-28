# Authentication

???+ info

    For a detailed discussion of how Shaken Fist authentication works, please see
    the discussion in the [developer guide](/developer_guide/authentication/).

Terms used here are defined in the [glossary](../glossary.md).

## Key expiry and reaping

Namespace keys may carry an optional expiry. Expiry is enforced when the key is
used, so an expired key stops authenticating the instant it lapses -- there is
no window during which it still works because a cleanup task has not run yet.

Tidying up expired keys is a separate, purely cosmetic concern. The cluster
daemon sweeps every fifteen minutes and soft-deletes keys which expired more
than `NAMESPACE_KEY_REAP_GRACE` seconds ago; the standard object reaper then
hard-deletes them once they have been soft-deleted for `CLEANER_DELAY`.

| Setting | Default | Effect |
|---------|---------|--------|
| `NAMESPACE_KEY_REAP_GRACE` | `3600` | Seconds after expiry before a key is soft-deleted. `0` disables reaping, retaining expired keys forever. |

The grace period is there so that an operator investigating automation which
suddenly stopped working can still see the key that lapsed. Lengthen it if you
want a longer forensic window; set it to `0` if you would rather expired keys
were never removed. Neither choice affects security, because enforcement does
not depend on the sweep having run.

## Keys and the event log

Credentials are deliberately absent from Shaken Fist's events. This is worth
knowing about because events are written to syslog *and* shipped to Loki, so
anything in an event has left the cluster and is sitting in log aggregation,
which usually has weaker access control than the credential does.

Events record the key *name* that was used, never the key secret, its stored
hash, its nonce, or any minted or presented token. Separately, the API request
tracing that records request and response bodies skips bodies entirely for
routes under `/auth`, since those carry plaintext key secrets inbound and
minted tokens outbound. The request URL is still logged, so you keep the
namespace and the key name.

If you have log tooling that greps for tokens in Shaken Fist events, it will
find nothing from these releases onward. That is the intent, not a regression.

## Upgrading

Keys used to live in a JSON column on the `namespace_attributes` table and are
now first-class objects in their own tables. Migration happens during
`sf-ctl ensure-mariadb-schema`, preserving each key's hash, nonce and expiry
exactly, so tokens minted before the upgrade keep validating and no operator
action is required.

The migration is one-way in the usual sense: keys created *after* the upgrade
are not written back to the old JSON column, so downgrading to a release that
predates this change loses them. Keys that existed before the upgrade are
unaffected either way. The exposure is one upgrade cycle, and it matches the
precedent set by the `node_daemon_states` migration.

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