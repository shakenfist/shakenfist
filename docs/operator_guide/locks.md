# Locks

Shaken Fist uses etcd for distributed locking. All locks are written into etcd
with the `/sflocks` key prefix. Locks are effectively leases on a key within
etcd, where the key contains metadata about the lock being held. This means its
easy to determine who else is holding a lock if you see contention issues within
your cluster.

The easiest way to do this is with the `sf-client admin lock list` command,
which will list all locks currently held in the cluster. For example, here's a
relatively idle cluster:

```
$ sf-client admin lock list
+----------------------+-------+------+---------------------+
|         lock         |  pid  | node |      operation      |
+----------------------+-------+------+---------------------+
| /sflocks/sf/cluster/ | 26407 | sf-7 | Cluster maintenance |
+----------------------+-------+------+---------------------+
```
