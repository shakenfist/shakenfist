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

## Watchdog-assisted lock failover

The lease mechanism above handles the case where a lock holder **dies**:
the holder's refresher thread exits with the process, the lease lapses
after at most 60s, and a candidate steals it. Until the watchdog was
added there was a gap: a holder whose **main loop wedged** while the
process remained alive would keep refreshing the lease forever via its
still-running refresher thread, blocking any failover indefinitely.

The systemd watchdog now closes that gap for the eight armed daemons
(database, net, cleaner, cluster, queues, resources, transfers,
sidechannel). Each daemon's main loop calls `Daemon.pet_watchdog()`
from `idle()` and, for long maintenance passes, at explicit points
inside the pass. If the loop stops petting, systemd delivers SIGABRT
after `WatchdogSec` (60s). The refresher thread dies with the process,
and the lease expires normally.

For the elected `sf-cluster` the effect is the full lock failover
chain:

1. Wedged elected daemon stops petting the watchdog.
2. Systemd kills the process after 60s and restarts it
   (`Restart=on-failure`).
3. The killed process's refresher thread exits; the `cluster/` lease
   (60s lifetime) lapses.
4. A standby `sf-cluster` steals the lock and takes over.

Worst-case failover is approximately **120s** (60s watchdog timeout + 60s
lease). No operator action is needed.

A tighter option — having the wedged daemon shed the lease before being
killed, via a signal handler that calls `ClusterLock.release()` — would
reduce the worst case to one lease lifetime (60s). That optimisation is
intentionally deferred: it adds process-signal complexity and the 120s
window is acceptable for the current maintenance workload.

## Stale-lock cleanup on restart

When a daemon starts up, its `queues` startup task calls
`locks.clear_stale_locks()` which deletes any rows still attributed
to that node by a process ID that no longer exists. This is no longer
required for correctness (the lease will time them out anyway) but it
keeps the table tidy after fast restarts.
