# Locks

Shaken Fist uses leased distributed locks backed by MariaDB. Each
held lock is a row in the `cluster_locks` table with an `expires_at`
timestamp. The holder's daemon refreshes the timestamp every ~20s
while it still wants the lock; if a holder dies (or is partitioned
from the database for too long), the row's lease lapses and another
candidate may steal it. There is no garbage-collection step or
external reaper -- a dead holder's lock recovers automatically the
next time anyone tries to acquire it.

The lease length is 60s and the refresh interval is one third of that
(20s), so a holder can lose two consecutive refreshes (e.g. through a
brief `sf-database` outage) and still keep the lock. These constants
are kept aligned in `shakenfist.locks.LEASE_SECONDS` and
`shakenfist.mariadb.CLUSTER_LOCK_LEASE_SECONDS`.

## Inspecting locks

The easiest way to see who is holding what is `sf-client admin lock
list`. For example, here's a relatively idle cluster:

```
$ sf-client admin lock list
+----------------+-------+------+---------------------+
|      lock      |  pid  | node |      operation      |
+----------------+-------+------+---------------------+
| cluster/       | 26407 | sf-7 | Cluster maintenance |
+----------------+-------+------+---------------------+
```

(Old lock keys carried a `/sflocks/` prefix when locks lived in etcd.
The MariaDB-backed table stores lock keys without that prefix.)

## Acquire semantics

`ClusterLock.acquire()` does up to two atomic writes:

1. `INSERT IGNORE` a fresh row -- claims an unheld key.
2. If (1) loses the duplicate-PK race, an `UPDATE ... WHERE
   expires_at < NOW()` to steal a row whose lease has lapsed.

`expires_at` is set server-side via `NOW() + INTERVAL 60 SECOND`, so
candidate clock skew across nodes does not affect lease ordering.

On successful `acquire()` the lock starts a daemon thread that
refreshes the row every 20s. The thread exits cleanly on `release()`
or when a refresh attempt finds the row no longer carries our
`lock_id` (i.e. another node stole the lease while we were absent).

## Lease loss handling

When the refresher confirms a lock has been stolen it sets
`ClusterLock.lost_event` (a `threading.Event`) and exits. Long-held
holders should poll this event between iterations of their critical
section and abort cleanly when it fires. The cluster maintainer's
inner loop already does this -- it sleeps on `lock.lost_event.wait(60)`
and re-enters election when the wait returns truthy.

`ClusterLock.release()` raises `shakenfist.exceptions.LockNotHeld` if
the database has no record of us holding the lock at release time;
the context-manager `__exit__` swallows it (the body's exception is
more interesting) but the noisy log is preserved.

## Transient database failures

A transient `sf-database` outage during a refresh round causes the
refresher to retry every ~2s rather than treat the failure as a
confirmed loss. As long as the database recovers within ~40s the
holder keeps the lock without a re-election. The `RefreshLock` gRPC
handler signals `UNAVAILABLE` on transient MariaDB errors so the
client side's standard retry path applies.

## Stale-lock cleanup on restart

When a daemon starts up, its `queues` startup task calls
`locks.clear_stale_locks()` which deletes any rows still attributed
to that node by a process ID that no longer exists. This is no longer
required for correctness (the lease will time them out anyway) but it
keeps the table tidy after fast restarts.
