---
issue_repo: https://github.com/shakenfist/cloudgood
issue_path: docs/galera.md
---

# Galera and WSREP replication

Every cloud control plane needs somewhere to keep its state. OpenStack keeps
its instance records, network definitions, and quota accounting in a
relational database, and so does almost every other orchestration system of
any size. That database becomes the single most important stateful thing in
the deployment: if it is unavailable, nothing can be created, deleted, or
even reliably listed, and if it is silently wrong then your cloud is
confidently lying to its users.

The near-universal answer to this problem in the OpenStack world is a
MariaDB (or MySQL) cluster running Galera. It is a genuinely interesting
piece of distributed systems engineering, and it behaves in ways that
surprise operators who arrive with intuitions built on traditional
asynchronous replication. Several of those surprises are the direct,
unavoidable consequence of how it achieves correctness, which makes it a
good candidate for the treatment this book likes to give things: go one
layer below the operational documentation, and the operational advice stops
being a list of rules to memorise and starts being obvious.

We're going to follow three things through the system. First a transaction,
from a client's `COMMIT` to the moment the change exists on every node.
Then a node, from healthy through lagging, ejected, and back to synced,
because that path explains flow control, backups, and cluster sizing all at
once. Finally the bytes on the wire, because once you know what the first
two spines put on the network you know exactly what your replication
network is worth to an attacker.

## What WSREP actually is

The first source of confusion is that "WSREP" and "Galera" are not the same
thing, despite being used interchangeably in most blog posts.

**wsrep** (Write Set REPlication) is an API. It is a set of hooks compiled
into the database server that allow an external library -- a "provider" --
to be notified about, and to veto, transaction lifecycle events. The
database server knows nothing about clustering. It knows that at certain
points (transaction start, commit, rollback, DDL execution) it must call
into whatever provider has been configured and do what it is told.

**Galera** is the provider. It is a shared library, usually
`libgalera_smm.so`, which implements the actual replication, group
membership, and conflict detection. This is why the configuration looks the
way it does:

```ini title="/etc/mysql/mariadb.conf.d/60-galera.cnf"
[galera]
wsrep_on                = ON
wsrep_provider          = /usr/lib/galera/libgalera_smm.so
wsrep_cluster_name      = openstack
wsrep_cluster_address   = gcomm://10.0.0.11,10.0.0.12,10.0.0.13
wsrep_node_address      = 10.0.0.11
wsrep_sst_method        = mariabackup
binlog_format           = ROW
default_storage_engine  = InnoDB
innodb_autoinc_lock_mode = 2
```

`wsrep_provider` is quite literally "load this shared object and hand it my
transactions". Everything else in that file is either addressing
information or a constraint that the provider requires in order to work,
and we'll meet the reasons for those constraints as we go (1).
{ .annotate }

1. This split is why the same Galera library can be loaded by MariaDB,
   Percona XtraDB Cluster, and (historically) MySQL builds patched with the
   wsrep hooks. The provider is common; the wsrep integration in each
   server differs slightly, which is the source of most of the
   version-compatibility trivia in this space.

## Spine one: following a transaction

The mechanism Galera uses is called **certification based replication**, and
it is fundamentally different from both traditional MySQL asynchronous
replication and from a classical two phase commit. It is worth walking
through slowly, because every operational behaviour later in this chapter
falls out of these six steps.

### Step one: the transaction runs locally and optimistically

A client connects to one node and runs a transaction. That transaction
executes entirely on that node. It reads local data, it takes ordinary
local InnoDB row locks, it builds up a set of changes in memory. No other
node in the cluster knows this transaction exists.

This is the optimistic part, and it is a deliberate design decision. There
is no distributed locking, no coordination round trip per statement, no
lock manager to become a bottleneck. The cluster is betting that most
transactions do not conflict with concurrent transactions on other nodes,
which for most real workloads is a very good bet.

Note the immediate consequence, which we will return to when we discuss
OpenStack: **the locks are local**. A `SELECT ... FOR UPDATE` on node one
takes a lock that node two knows nothing about and does not respect. If
your application's correctness depends on `FOR UPDATE` providing mutual
exclusion, it does not provide it across a Galera cluster.

### Step two: the write set is extracted

When the client issues `COMMIT`, the node does not commit. Instead, it
assembles a **write set**, which contains two distinct things:

The changes
:   The actual row modifications, in row based binary log format. Not the
    SQL statement -- the before and after images of every row the
    transaction touched. This is why `binlog_format=ROW` is mandatory: a
    statement like `UPDATE foo SET bar = NOW()` is not deterministic, and
    replaying the statement on another node could produce different data.
    Replaying the resulting rows cannot.

The keys
:   A list of the primary keys, unique keys, and foreign keys of every row
    the transaction touched. These are used for conflict detection, and
    they are the reason Galera requires every table to have a primary key.
    A table without one gives the provider nothing to compare against, so
    conflicts on that table cannot be detected reliably (1).
{ .annotate }

1. This is a genuine correctness requirement, not a performance
   recommendation. Row ordering on a table with no primary key is not
   guaranteed to be the same on every node, so operations on such tables
   can produce divergent data. Modern MariaDB will warn or refuse
   depending on configuration; if you are migrating a legacy schema into
   Galera, auditing for missing primary keys is the first job.

### Step three: total order broadcast

The write set is handed to Galera's group communication layer, **gcomm**,
and broadcast to every node in the cluster including the originating one.

gcomm's guarantee is the load bearing trick of the entire system: every
node receives every write set in **exactly the same order**, and each write
set is assigned a monotonically increasing global sequence number, the
**seqno**. The cluster's position is expressed as the cluster UUID plus
this seqno, which is what a Galera GTID is (1).
{ .annotate }

1. You can see the current position at any time with `SHOW STATUS LIKE
   'wsrep_last_committed'` for the seqno, and `wsrep_cluster_state_uuid`
   for the UUID half. The pair is also written to `grastate.dat` in the
   data directory on clean shutdown, which is how a restarting node knows
   where it was.

It is difficult to overstate how much work this ordering does. A hard
distributed problem -- "do these two concurrent transactions on different
machines conflict, and if so which one wins?" -- has been converted into a
purely local, deterministic question, because every node is now looking at
the same sequence of events in the same order. No node needs to ask any
other node anything in order to answer it.

### Step four: certification

Each node now independently runs **certification** on the write set. The
question being asked is narrow and specific:

> Does this write set's key set intersect with the key set of any write set
> that was ordered *before* it, but which has not yet been applied on this
> node?

Each node maintains a certification index of recent write sets and their
keys. Certification is an interval comparison against that index. If there
is no overlap, the write set passes. If there is an overlap, it has lost a
race against a transaction that got a lower seqno, and it fails.

Because the input (the ordered sequence of write sets) is identical on
every node, and the algorithm is deterministic, **every node reaches the
same verdict independently and simultaneously**, with no additional network
traffic and no voting. This is the elegant core of Galera. It is not
consensus in the Paxos or Raft sense; consensus was already achieved by the
total ordering, and certification is just a deterministic function applied
to the agreed sequence.

### Step five: apply, or shoot the loser

If the write set passes certification, every node applies it and commits.
On remote nodes this is done by applier threads, which is fast because
there is no SQL to parse -- they are applying row images directly.

If it fails certification, the outcome depends on where you are:

- On the **other** nodes, the write set is simply discarded. Nothing
  happened, no client is waiting, no harm done.
- On the **originating** node, the transaction that has been sitting there
  in `COMMIT` is rolled back, and the client receives a deadlock error
  (`ER_LOCK_DEADLOCK`, error 1213).

This is the single most surprising Galera behaviour for application
developers, and it is worth stating plainly: **a transaction can fail with
a deadlock error at commit time, having successfully acquired every lock it
asked for, with no local lock contention whatsoever.** The conflict was
with a transaction on a completely different machine that the local node
could not have known about. First committer wins; the loser is shot.

Any application running against a Galera cluster must therefore be prepared
to retry transactions on deadlock. Applications that assume a successful
`UPDATE` followed by a successful `COMMIT` cannot fail will corrupt their
own logic eventually.

### Step six: what "committed" actually means

Galera is usually described as "virtually synchronous", and the modifier is
carrying real weight.

The *replication* is synchronous: when your `COMMIT` returns success, the
write set has been ordered globally and certified, and it is guaranteed
that it will be applied on every node. The *application* of the change is
asynchronous: it may not have been applied yet on the node your colleague
is reading from.

So a successful commit means "this change is now inevitable everywhere",
not "this change is visible everywhere". For most purposes that is
sufficient. When it is not -- when you need read-your-writes semantics
against a different node than the one you wrote to -- Galera provides
`wsrep_sync_wait`, which makes a session block at the start of a statement
until the node has applied everything that was committed cluster-wide at
the time the statement began (1).
{ .annotate }

1. `wsrep_sync_wait` is a bitmask controlling which statement types wait:
   1 for `SELECT`, 2 for `UPDATE`/`DELETE`, 4 for `INSERT`/`REPLACE`, and
   so on. Setting it to 1 is the common choice. It costs latency on every
   affected statement, so it is usually set per session by the code that
   actually needs it rather than globally.

???+ info

    Is any of this specific to SQL? Much less than you would think. The
    wsrep API describes itself as a generic replication interface for
    "DBMS-like applications", and it means it. Write sets are opaque byte
    buffers (`wsrep_buf_t`), and certification keys are arrays of opaque
    key parts (`wsrep_key_t`) which MySQL merely happens to populate with
    a database name, a table name and a primary key. An integrator
    supplies a handful of callbacks -- apply a write set, request a state
    transfer, donate one, handle a membership change -- and everything
    described in this chapter comes for free. Nothing in the provider
    knows what a table is.

    In practice nobody outside the MySQL family ever shipped an
    integration, so the generality remains theoretical. It is fun to
    speculate, though. LDAP would fit unusually well in places: a
    distinguished name is already an ordered hierarchy of components,
    which is exactly the shape `wsrep_key_t` wants, and a single LDAP
    write is a natural certification unit. What spoils it is that
    directory workloads are overwhelmingly reads, so there are few write
    conflicts worth solving, and directory replication deliberately
    resolves the ones it has per-attribute with last writer wins. Two
    administrators editing different attributes of the same user entry
    both succeed today; under certification that is one entry, so one of
    them would eat a deadlock. Galera would be selling a stronger
    guarantee than directories want at a price they would rather not pay.

    The group communication half, by contrast, escapes the database world
    constantly. gcomm is a relative of the Totem protocol that corosync
    implements, which is quite likely already running in the same control
    plane, doing membership and quorum for Pacemaker.

## Spine two: following a node

The happy path above assumes every node keeps up. The interesting operational
behaviour of Galera is entirely about what happens when one does not.

### Flow control: how a slow node stops the cluster

Because apply is asynchronous, a node that receives write sets faster than
it can apply them accumulates a **receive queue**. Left unchecked, that node
would fall arbitrarily far behind, and the "virtually" in virtually
synchronous would stretch to breaking point.

Galera's answer is **flow control**. When a node's receive queue exceeds a
threshold (`gcs.fc_limit`, which scales with cluster size), that node
broadcasts a flow control pause message, and *every node in the cluster
stops committing new transactions* until the struggling node has drained
its queue below a resume threshold.

Read that again, because it is the most important operational sentence in
this chapter: **one slow node pauses the entire cluster**. Your write
throughput is not the average of your nodes, and it is not the throughput
of the node your load balancer is pointing at. In steady state, cluster
wide write throughput converges on the apply rate of the slowest member.

All of this is observable, but -- and this catches people out -- almost
entirely through status variables rather than through logs. These are the
counters worth putting on a dashboard before you need them, and several of
them have a sharp edge worth knowing about:

`wsrep_local_recv_queue`
:   The current length of the receive queue: how many write sets are
    waiting to be applied right now. On a healthy node this sits near zero.
    An instantaneous snapshot, so it is honest but twitchy.

`wsrep_local_recv_queue_avg`
:   The average queue length **since the most recent status query**. A
    consistently non-zero value means this node is the bottleneck, and you
    have found your slow node. Note those reset semantics: reading the
    variable clears it, so the figure describes the interval since whoever
    last looked, which may have been your monitoring system a second ago.

`wsrep_flow_control_paused`
:   The fraction of time since the last `FLUSH STATUS` that replication was
    paused by flow control. A value of 0.3 means the cluster spent thirty
    percent of that period frozen. This is the number that explains "the
    database was slow" reports which show nothing whatsoever on the writer.
    Beware that it averages over the entire period since the last reset, so
    on a server with three months of uptime a brutal two hour stall
    yesterday rounds to approximately nothing.

`wsrep_flow_control_paused_ns`
:   The same quantity expressed as raw nanoseconds paused rather than a
    fraction, and monotonic until somebody runs `FLUSH STATUS`. This is the
    one to graph: differencing consecutive samples gives pause time per
    interval, which is what you actually wanted, and it is immune to the
    averaging problem above.

`wsrep_flow_control_sent` and `wsrep_flow_control_recv`
:   Counts of flow control pause events this node has sent, and has
    received plus sent, **since the most recent status query**. A non-zero
    `sent` means this node is the one doing the throttling rather than a
    victim of it, which is usually the question you actually want answered.
    These reset on read as well.

`wsrep_cert_deps_distance`
:   The average distance in the sequence between transactions that could
    possibly have been applied in parallel. It is an upper bound on useful
    applier parallelism for your workload, and therefore a sensible
    starting point for sizing applier threads.

!!! warning

    Three of those variables reset when they are queried, not on a timer
    and not on restart. If two things poll the same node -- say Prometheus
    every fifteen seconds and you at a `mysql` prompt -- they consume each
    other's counts, and your interactive reading will look reassuringly
    small because the scraper just took the interesting part. When
    investigating by hand on a monitored cluster, trust
    `wsrep_flow_control_paused_ns` and `wsrep_local_recv_queue`, which do
    not have this behaviour.

### What gets logged? Almost nothing

If you arrived at this section from the slow query log, the analogy
unfortunately does not hold. **Flow control is silent.** At default
settings a Galera node writes nothing to the error log when it engages flow
control, nothing when the cluster pauses, and nothing when it resumes.

The reason is defensible once you see the scale involved. Flow control is
not an error condition, it is ordinary backpressure, and on a busy cluster
it can engage and release many times per second. An error log entry per
event would generate more write traffic than the workload being throttled.

What the error log does contain is state transitions and membership
changes: a node shifting between `SYNCED`, `DONOR/DESYNCED`, `JOINER` and
`JOINED`, group view changes as members come and go, and the beginning and
end of state transfers. So the log tells you about the dramatic events and
says nothing at all about the chronic condition. A cluster can spend a
third of its life frozen by a struggling node and produce a completely
clean error log, which is precisely why this trips people up.

There are two switches worth knowing about:

`gcs.fc_debug=N`
:   Set inside `wsrep_provider_options`, default 0 (disabled). Makes the
    provider post flow control debug statistics to the error log every `N`
    write sets. Note that it is triggered by volume rather than by events,
    so it produces output steadily whether anything is wrong or not. This
    is a tool for a bad afternoon with a reproducible problem, not
    something to leave enabled.

`wsrep_log_conflicts=ON`
:   A server variable which logs the details of certification conflicts --
    the transactions involved and the conflicting keys -- to the error log.
    This does not help with flow control directly, but it is what turns the
    commit-time deadlocks from step five into something diagnosable, and
    anybody investigating one of these is usually about to want the other.
    Conflicts should be rare enough that leaving this on is inexpensive.

Beyond those, the answer is that the status variables *are* the interface,
and you are expected to sample them into whatever monitoring you already
have. The `mysqld_exporter` Prometheus exporter exposes the `wsrep_`
variables, and Percona Monitoring and Management ships Galera dashboards
built on exactly the counters above.

???+ tip "Go and look at your own cluster now"

    On each node in turn:

    ```sql
    SHOW GLOBAL STATUS WHERE Variable_name IN (
        'wsrep_local_state_comment',
        'wsrep_local_recv_queue',
        'wsrep_local_recv_queue_avg',
        'wsrep_flow_control_paused',
        'wsrep_flow_control_paused_ns',
        'wsrep_flow_control_sent',
        'wsrep_cert_deps_distance');
    ```

    Run it on every node, not just the writer, because the node causing the
    problem is by definition not the one you are talking to. A node with a
    non-zero `wsrep_flow_control_sent` is throttling the cluster. Remember
    that this query resets three of these counters, so the second run tells
    you about the gap between your two runs -- which, if you wait a minute
    between them, is actually a rather useful measurement.

### Why a node ends up slow

Several causes, most of them unrelated to the node's hardware being
inadequate:

**Insufficient applier parallelism.** The originating node ran the workload
at full client concurrency -- perhaps two hundred connections. The remote
nodes apply it with `wsrep_slave_threads` applier threads, which in many
distributions still defaults to 1 (1). A cluster can be perfectly healthy
at low load and fall off a cliff at moderate load purely because of this.
{ .annotate }

1. Newer MariaDB versions call this `wsrep_applier_threads`, with
   `wsrep_slave_threads` retained as an alias. Values in the range of 2 to
   4 times the CPU core count are commonly suggested, bounded by what
   `wsrep_cert_deps_distance` says your workload can actually exploit.

**One enormous transaction.** A single write set containing a million rows
is a single unit of apply work. It cannot be parallelised, and it stalls
the pipeline on every node. The `DELETE FROM huge_table` that would merely
be slow on a standalone server becomes a cluster wide outage. Galera 4
added streaming replication, which fragments large transactions into
multiple write sets that replicate and certify progressively, but it is
opt-in and comes with its own costs.

**DDL under total order isolation.** By default, schema changes are
replicated as statements and executed under **TOI** (Total Order
Isolation): the statement takes its place in the global sequence, and every
node stops and executes it at that point. A ten minute `ALTER TABLE` is
therefore a ten minute cluster wide write outage. The alternative,
**RSU** (Rolling Schema Upgrade, via `wsrep_OSU_method=RSU`), desyncs one
node at a time to apply the change locally, which avoids the stall but
requires the schema change to be replication-compatible in both directions
while the rolling upgrade is in progress. In practice most large
deployments use an external online schema change tool (`gh-ost`,
`pt-online-schema-change`) instead.

**Being a donor.** A node feeding a state transfer to a joining node is
busy doing that, which we'll get to shortly.

**Resource limits you forgot about.** A Galera node running in a container
or under a systemd slice is subject to the cgroup limits described in the
[CPU and resource accounting](/components/cloudgood/accounting/) chapter. An IO bandwidth limit
that was invisible on a standalone server becomes a cluster wide throughput
ceiling the moment that node is the slowest applier.

### Desync: formally excusing a node

Galera provides an explicit escape hatch. Setting `wsrep_desync=ON` tells
the cluster that this node is no longer to be considered when calculating
flow control. It continues to receive and queue every write set, it remains
a full voting member of the cluster, it has lost no data -- it is simply
permitted to fall behind without dragging everyone else down with it.

The node reports its state as `Donor/Desynced` in
`wsrep_local_state_comment`, which is the same state a node enters when
feeding a state transfer, because feeding a state transfer is exactly a
case of "this node needs to be allowed to lag for a while".

When you set `wsrep_desync=OFF`, the node applies its accumulated backlog
as fast as it can and returns to `Synced`. During that catch-up it is
still not sending flow control, so the cluster is not affected.

!!! note

    A desynced node will happily serve stale reads, and gets no less stale
    the longer it stays desynced. Removing it from the load balancer's read
    pool is part of the procedure, not an optional extra.

### The gcache: a ring buffer, not a log

Every node keeps a **gcache**: an on-disk ring buffer of recent write sets,
sized by `gcache.size` (default 128MB, which is almost always too small for
a busy cluster).

It exists for exactly one purpose: so that a node which has been briefly
absent can be caught up by replaying the write sets it missed, rather than
being given a full copy of the database. Because it is a ring buffer, old
write sets are overwritten as new ones arrive. It is not durable, it is not
archived, and -- this matters enormously and we'll return to it -- it is
not a binary log.

### IST versus SST

When a node joins or rejoins the cluster, it announces the seqno it last
saw. The cluster then chooses between two paths.

**IST** (Incremental State Transfer) happens when the joiner's last seqno
is still present in some donor's gcache. The donor simply replays the
missing write sets over a dedicated connection, the joiner applies them,
and it is caught up. This is fast, proportional to how long the node was
away, and imposes little load on the donor.

**SST** (State Snapshot Transfer) happens when the joiner's position has
aged out of every gcache, or when the joiner has no usable state at all --
a brand new node, or one whose `grastate.dat` shows an unsafe shutdown. The
joiner's data directory is discarded and replaced with a complete copy from
a donor.

Whether a given restart gets the fast path or the slow path is therefore
decided entirely by `gcache.size` versus your write rate and outage
duration. Sizing the gcache to comfortably cover your longest expected
maintenance window is one of the highest value pieces of Galera tuning
available, and it is nearly free -- disk is cheap and the buffer is
preallocated.

#### Who gets chosen as the donor, and why it is probably wrong

Nothing so far has said which node does the donating, and the answer
deserves attention because the default behaviour has a sharp edge.

Left to itself, Galera selects a donor by preferring one that can serve an
IST -- that is, one whose gcache still holds the joiner's seqno -- honouring
the joiner's `wsrep_sst_donor` list first if one is set, and otherwise
preferring a node in the same `gmcast.segment` as the joiner. Every one of
those criteria is about minimising the cost of the transfer: less data to
ship, over a cheaper link.

Look at what is absent from that list. Nothing considers the candidate's
IO capacity, its current load, or how much client traffic it is serving.
Most importantly, **Galera has no idea which node is your writer**, because
that fact lives entirely in your load balancer's configuration and the
cluster cannot see it. From the provider's point of view every node is an
identical peer.

The consequence is a genuine three in the morning hazard: a node restarting
unattended can select your active writer as its donor, desyncing it and
putting it under transfer load while it is serving every write in the
deployment. Nobody chose that; it happened because the writer had the right
seqno in its gcache and was in the right segment. It is the same "protect
the node taking client traffic" concern as the backup procedure discussed
later in this chapter, arriving from the opposite direction and without
anybody asking for it.

The fix is to set `wsrep_sst_donor` on each node, naming donors you are
willing to lose, in order of preference. One detail matters when you do: a
terminating comma at the end of the list means "and if none of those are
available, any other node will do", while its absence means the joiner
waits rather than pressing an unsuitable node into service. Which you want
is a real decision -- a slow rejoin, or a surprised writer.

The SST methods available are worth knowing individually, because they have
very different consequences:

`mariabackup`
:   The modern default. The donor runs `mariadb-backup`, streaming a
    physical copy of the InnoDB files to the joiner. Because it uses
    backup-style locking rather than a global read lock, the donor stays
    usable throughout. This is the one you want.

`rsync`
:   Fast, and requires no database credentials, but blocks the donor for
    the duration of the transfer. Acceptable on small datasets, painful on
    large ones.

`mysqldump`
:   A logical dump and reload. Locks the donor, produces a vastly larger
    transfer, and takes far longer to load. It is legacy; there is no
    modern reason to choose it as a state transfer method. Note that this
    is a separate question from whether `mysqldump` is a reasonable
    *backup* tool on an older cluster, which is discussed later in this
    chapter.

### Quorum, split brain, and the arbitrator

The last piece of node lifecycle is membership. gcomm implements a virtual
synchrony membership protocol: nodes continuously agree on who is present,
and when that set changes, they recompute.

The rule is that a partition of the cluster containing **more than half**
of the last known members remains the **Primary Component** and continues
to serve traffic. Any node not in the Primary Component refuses queries
with `ERROR 1047 (08S01): WSREP has not yet prepared node for application
use`, which is Galera declining to become a split brain rather than a node
being broken.

This is why the two node cluster is a trap. Lose either node and neither
survivor has more than half, so both stop serving. A two node Galera
cluster has *worse* availability than a single server, while costing twice
as much. The answer is either a third database node, or **garbd**, the
Galera arbitrator: a small daemon that joins the group, participates in
ordering and voting, and stores no data. It is enough to break ties and it
can run on any convenient host.

## What actually determines cluster performance

We now have enough machinery to answer the question properly, and the
answer is that "a cluster is as fast as its slowest node" is true but
conflates two separate mechanisms with different causes and different
fixes.

**Commit latency is set by the worst network path.** Every commit waits for
the write set to be broadcast and ordered, and that is a group agreement,
so the originating node waits on the round trip to the *farthest* node.
Note what is *not* involved: the remote nodes' disks and CPUs are
irrelevant to this number, because they have not applied anything yet, they
have only acknowledged ordering. This is why putting a Galera cluster
across a WAN inserts the WAN round trip time into every single commit, and
why adding nodes never makes writes faster -- it can only lengthen the tail
of that broadcast (1).
{ .annotate }

1. Galera's **segments** feature (`gmcast.segment` in
   `wsrep_provider_options`) mitigates the WAN case by grouping nodes by
   site, so that a write set crosses the expensive link once per site
   rather than once per node. It reduces bandwidth and the impact of the
   slow link, but it cannot remove the round trip from the commit path.

**Sustained throughput is set by the worst apply rate**, with flow control
as the coupling mechanism, exactly as described above.

These call for different responses. High commit latency with empty receive
queues everywhere is a network problem, and no amount of applier tuning
will help. Acceptable latency with a growing queue on one node is an apply
problem, and the fix is applier threads, IO capacity, or removing whatever
is competing for resources on that node.

There is one more performance lever which deserves an honest mention.
Because a committed transaction is guaranteed to exist on every node, many
operators run Galera nodes with `innodb_flush_log_at_trx_commit=2`, which
stops InnoDB fsyncing the redo log on every commit and can improve apply
throughput dramatically. The reasoning is that the cluster, rather than the
local disk, is now the durability mechanism. This is a defensible trade,
but it is a real one: it is safe against the loss of a single node, and it
is not safe against simultaneous loss of the whole cluster, which is
precisely what a data centre power event looks like. Make the choice
deliberately rather than by copying a tuning guide.

### Adding nodes does not make it faster

This deserves its own heading, because it is the most common instinct
operators bring to a struggling cluster and it is precisely backwards.

The intuition is imported from stateless tiers, where adding a web server
adds capacity because each new server handles a share of the requests.
Galera does not work that way, and the reason is structural: **there is no
partitioning, so every node applies every write.** A five node cluster does
not process a write five times faster than a one node cluster; it processes
the same write five times. The write capacity contributed by an additional
node is not small, it is exactly zero.

Both of the mechanisms above then get worse rather than better. Commit
latency degrades because the total order broadcast now has more members to
reach, and the originating node waits on the slowest of them. Sustained
throughput degrades because the cluster runs at the apply rate of its
slowest member, and that is a minimum over the set of nodes -- adding
another sample to a minimum can only lower it or leave it unchanged. It can
never raise it. Every node you add is another opportunity to discover that
one of your machines has a degraded RAID array.

So if your instinct is that the fastest possible Galera cluster is a single
node one, that instinct is correct. It is also, obviously, the least
reliable one, and that is the entire trade: those extra nodes are not
buying throughput, they are buying availability, and the price is paid in
write performance (1).
{ .annotate }

1. To be strictly accurate, a single node cluster is still slower than the
   same machine with `wsrep_on=OFF`, because write set extraction and
   certification against the local index continue to happen even with
   nobody to replicate to. The overhead is modest, but "one node Galera"
   and "standalone MariaDB" are not quite the same performance profile.

There is one real exception, and it is the reason adding nodes is not
simply a mistake: **reads do scale.** Every node holds the complete
dataset, so read traffic genuinely can be spread across the cluster, and a
five node cluster really can serve more concurrent `SELECT` traffic than a
three node one -- provided the readers tolerate the staleness window from
step six, or pay for `wsrep_sync_wait` where they cannot.

But that exception comes with a trap that catches people regularly.

!!! warning

    Every node must be able to apply the entire write workload, no matter
    what you intend to use it for. There is no such thing as a small read
    replica in a Galera cluster. Adding an undersized machine "just for
    reporting queries" gives it the full write stream to apply anyway, and
    if it cannot keep up it becomes the slowest applier -- which, via flow
    control, becomes the write throughput ceiling for the whole cluster.
    A node added to help can very easily make everything slower.

The useful reframing is that Galera is a *redundancy* cluster rather than a
*scale-out* cluster, and the word "cluster" is doing you a disservice by
implying otherwise. Three nodes is the standard recommendation because it
is the smallest number that provides quorum, not because it is fast.

Which leaves the question of what does actually make a cluster faster, and
the answers are all about raising the floor rather than adding to the
ceiling: make the slowest node quicker, give the appliers enough threads to
exploit the parallelism `wsrep_cert_deps_distance` says your workload has,
break up the enormous transactions and the TOI schema changes that
serialise the pipeline, and use segments if geography forces your hand. If
you genuinely need more write throughput than one machine can apply, no
number of Galera nodes will provide it, and the answer lies in sharding or
a different architecture entirely.

## Backups: the arrow points the other way

A question that comes up regularly is whether an SST is an interesting way
to obtain a backup, or whether it is just `mysqldump` with extra steps. The
framing is backwards, and inverting it is illuminating: **a modern SST is
your backup tool, reused as replication machinery**. The `mariabackup` SST
method literally runs `mariadb-backup` on the donor and streams the result
to the joiner, which prepares and restores it.

That has a genuinely useful consequence. If your SST method is
`mariabackup`, then every node join exercises your backup *and restore*
path end to end, under production load, with the result validated by the
node subsequently joining and serving traffic. That is dramatically more
restore testing than most backup regimes ever receive.

You can also invoke the machinery deliberately: `garbd` can be pointed at
the cluster to request a state transfer purely to capture a backup, using a
`wsrep_sst_backup` script on the donor. You get a physical backup stamped
with the cluster's replication position, produced by exactly the same code
path as a node join.

Note two things before reaching for it, though. You name the donor yourself
with `--donor`, which given the previous section's discussion of automatic
donor selection is a feature rather than an inconvenience -- but it does
mean the technique offers no help whatsoever in *choosing* a node. And
Galera does not ship a backup SST script: `wsrep_sst_backup` has to be
adapted by hand from one of the existing `wsrep_sst_*` scripts. The
established pattern is to wrap the whole thing in something that first
verifies the intended donor is Synced and that enough nodes remain, which
amounts to hand-writing the selection logic anyway. It is a clever
demonstration that state transfer and backup are the same operation, and it
is rarely used.

That is largely because you do not need the SST machinery for this, since
running `mariadb-backup --backup --galera-info` against any single node
gives you the same physical backup with the same position recorded in
`xtrabackup_galera_info`, with far fewer moving parts. So the real value of
understanding SSTs is not that you should back up through them; it is that
they teach you what a correct cluster backup *is*: a single node's physical
snapshot, plus a global sequence number.

### Should you back up on the writer?

In a Galera cluster there is no master. Every node holds the full dataset
and any node is a valid backup source; the "primary" is merely whichever
node your load balancer sends writes to. So the traditional "spare the
primary, back up a replica" reasoning does not transfer directly. The real
argument is about load, and it has a Galera-specific sting.

**Without desync, backing up a non-writer node can hurt the writer more
than backing up the writer would have.** On the writer, a backup competes
for IO and pollutes the buffer pool, and commits get somewhat slower --
a degradation. On a non-writer, the backup slows that node's *apply* rate,
its receive queue grows past `gcs.fc_limit`, it emits flow control, and the
entire cluster stops committing. The isolation you believed you were buying
by avoiding the writer is defeated by the coupling mechanism. A struggling
Galera node does not lag quietly like an asynchronous replica; it reaches
out and throttles everybody.

So the procedure is a ritual, and every step maps to a mechanism we have
already met:

- [x] Remove the node from the load balancer's read pools, because it is
      about to start serving stale data.
- [x] `SET GLOBAL wsrep_desync=ON`, so its lag stops throttling the
      cluster.
- [x] Take the backup.
- [x] `SET GLOBAL wsrep_desync=OFF` and wait for `wsrep_local_recv_queue`
      to return to approximately zero.
- [x] Return the node to the load balancer.

"Sacrifice a node to take the backup" is the right instinct, but it
overstates the cost: the node never leaves the cluster, never loses data,
and votes throughout. What you actually spend is a node's worth of read
capacity, and some redundancy margin -- while desynced, that node's on-disk
state is minutes stale, so this is a poor moment to lose a second node.
Which gives the obvious corollaries: never desync more than one node at a
time, and in a three node cluster treat the backup window as a period of
reduced safety worth scheduling thoughtfully.

Should you *never* back up on the writer? "Never" is too strong --
`mariadb-backup` under MariaDB's `BACKUP STAGE` locking is only briefly
blocking, and on a lightly loaded cluster nobody would notice. But there is
little reason to, because identical data exists on nodes whose slowdown you
can render harmless with a single `SET GLOBAL`, and that option does not
exist on the node taking client commits. Desyncing the writer would not
help it, since its load is the commit path itself rather than apply lag.

### Backing up a cluster that predates mariabackup

Everything above assumes `mariadb-backup` is available. Plenty of clusters
in the wild are older than that, and their operators frequently arrive at
the conclusion that Galera simply cannot be backed up, because the obvious
thing to reach for fails immediately. It is worth working through why,
because the failure is real and the conclusion is wrong.

Running a plain `mysqldump` against a Galera node produces table locking
errors. This is not a misconfiguration -- **explicit locking is a
documented Galera limitation.** `LOCK TABLES`, `FLUSH TABLES <explicit
list> WITH READ LOCK`, `GET_LOCK()` and `RELEASE_LOCK()` are all listed as
unsupported. And mysqldump's default behaviour is `--lock-tables`, so a
bare invocation issues precisely the statement Galera does not support.
That default is a holdover from the MyISAM era and has been the wrong
choice for InnoDB for roughly two decades, but it is still the default.

The three locking behaviours fail differently, and telling them apart is
most of the diagnosis:

Explicit `LOCK TABLES`
:   mysqldump's default. Unsupported by Galera, fails immediately.
    Desyncing the node does not help, because the problem is not flow
    control.

Global `FLUSH TABLES WITH READ LOCK`
:   With no explicit table list, this *is* supported -- it is what
    `--lock-all-tables` and `--master-data` use. But it stops the node
    committing, which propagates through flow control and stalls the whole
    cluster. This is the case `wsrep_desync` is the documented workaround
    for.

`--single-transaction`
:   Takes a consistent snapshot through InnoDB's MVCC and acquires no table
    locks at all. This is the documented recommendation, and it is the
    answer to the original error.

So the immediate unblock is the flag that has existed since roughly 2003:

```bash
mysqldump --single-transaction --skip-lock-tables \
          --all-databases > cluster.sql
```

Keep the two motivations distinct while you are doing it: `wsrep_desync`
protects the *cluster* from your backup, and `--single-transaction` is what
makes the *dump* succeed. Neither substitutes for the other.

There is one genuine caveat. `--single-transaction` does not produce a
consistent snapshot in the face of concurrent DDL, and on Galera, DDL
arrives from other nodes under total order isolation and will brute force
abort your dump's transaction when it does. That surfaces as
`mysqldump: Error 1213: Deadlock found when trying to get lock` partway
through a table, which is a well documented Galera experience. It is far
more likely on a busy writer than on a quiet node, which is one more reason
to pick your backup node deliberately.

!!! warning

    Point the backup at a specific node by name, never at the load
    balancer's virtual address. Two things go wrong otherwise: you get
    whichever node is currently taking all the writes, which maximises your
    exposure to the DDL problem above; and if you then desync it, the
    `clustercheck` health check reports it unavailable and the load
    balancer removes it, cutting the application's database connections
    along with your own.

Before settling for logical dumps, though, challenge the premise. The
reason `mariadb-backup` exists at all is that Percona XtraBackup stopped
supporting MariaDB after 10.2 -- so a cluster too old for mariabackup is
very likely squarely inside XtraBackup's supported range. "Too old for
mariabackup" is an argument *for* xtrabackup, not against physical backups.
Check what the cluster already does:

```sql
SHOW VARIABLES LIKE 'wsrep_sst_method';
```

If that returns `xtrabackup-v2`, the tool is already installed, already
configured, and already runs in production every time a node joins the
cluster. This is the point from the beginning of this section arriving in
its most useful form: the backup software you were looking for has been
doing your state transfers for years.

Finally, the option worth preferring for an old cluster if you can afford
the hardware: hang a **conventional asynchronous replica** off one node.
Enable `log_bin` and `log_slave_updates` on that node and attach an
ordinary MariaDB replica to it. The replica is not a cluster member, so it
cannot emit flow control, cannot be chosen as a donor, and cannot stall
anything. You can then back it up as brutally as you like -- lock every
table, stop it and copy the data directory, snapshot the filesystem -- with
no desync ritual and no lock semantics to reason about. It also produces
the binary logs discussed next, which Galera never requires and which you
almost certainly want.

### The gcache is not a binary log

This is the most important backup-adjacent misconception in Galera
deployments, and it follows directly from the ring buffer we described
earlier.

A restored backup is a snapshot at some seqno. If that seqno is still in a
donor's gcache, the restored node ISTs and rejoins cheaply. If it has aged
out -- and for a backup more than a few hours old it certainly has -- the
node takes a full SST instead. The gcache does not extend your recovery
options; it only decides how expensive rejoining is.

More importantly, Galera does not require the binary log to be enabled, and
so a great many deployments do not enable it. Those deployments have no
point in time recovery. If somebody runs a `DELETE` without a `WHERE`
clause, the cluster will faithfully, synchronously, and with perfect
consistency replicate that deletion to every node in a few milliseconds.
Restoring to the moment before it requires binary logs, which the cluster
never asked you for.

!!! warning

    "We run a three node Galera cluster" is a high availability story, not
    a backup story. Replication protects you from a node dying. It offers
    no protection whatsoever from a bad query, a bad migration, or a bad
    actor, because it will replicate all three flawlessly. You still need
    periodic physical backups taken off the cluster, and binary logs if you
    want to recover to a point in time between them.

## Spine three: following the bytes

Everything above describes information moving between machines. It is worth
asking what that traffic is worth to somebody who can see it, both because
it is a genuine hardening question and because the answer reinforces how
the system works.

A Galera cluster uses three ports, and they carry quite different things:

`4567/tcp`
:   gcomm group communication. Membership, ordering, and the continuous
    stream of write sets. Every committed transaction in the cluster
    crosses this port, to every node, as it happens.

`4568/tcp`
:   IST. Write sets replayed from a donor's gcache to a rejoining node.

`4444/tcp`
:   SST. A complete copy of the database.

The SST port is the alarming one, because an SST is by construction a full
copy of everything. With the `mariabackup` method it is an xbstream archive
of the InnoDB data files; with `rsync` it is the raw data directory over
the rsync protocol. Neither is encrypted unless you have explicitly
configured that. Reassemble such a stream, run `mariadb-backup --prepare`,
and you have a working copy of the database.

The replication traffic is a change log rather than a snapshot. Write sets
are row images with typed values, so they yield data readily; column
*names* depend on the server's row metadata settings, so you may be reading
positional columns and inferring meaning. DDL is more forthcoming, because
under TOI Galera ships the literal `CREATE TABLE` and `ALTER TABLE`
statements as SQL. And because every write set carries its seqno, ordering
never has to be inferred -- the same total ordering that makes certification
deterministic makes a captured stream unambiguous to replay.

Could you reconstruct the database from captured traffic? Yes, and almost
tautologically so: an SST plus the subsequent write set stream is precisely
what a joining node consumes in order to become a byte-identical replica.
Note, though, that IST is the *exception* path -- it only occurs when a node
rejoins, so it leaves gaps. The continuous record is the ordinary
replication traffic on 4567, which carries every transaction all the time.

But the passive capture scenario is the hard way to do this, and fixating
on it misses a larger hole. Galera's join path has historically had no
meaningful node authentication of its own: reach port 4567 with the correct
`wsrep_cluster_name` and you are a cluster member, at which point the
cluster *hands* you a full SST because that is what it does for new nodes.
A hostile joiner does not need database credentials for this;
`wsrep_sst_auth` is used by the donor to run the backup against itself, not
to authenticate the joiner.

The conclusion to draw is that **the replication network is the cluster's
security boundary**. Anyone inside it can read everything passively or
simply ask to be given everything, and anyone who can merely cause a node
to restart can compel a full copy of the database onto the wire on demand.
The replication network belongs on isolated infrastructure, and it should
be encrypted.

The practical trap is that this takes more than one switch. Configuring
`socket.ssl_key`, `socket.ssl_cert` and `socket.ssl_ca` in
`wsrep_provider_options` protects 4567 and 4568. It does **not** encrypt
SST on 4444, which is configured separately -- MariaDB will use TLS for SST
when the server's `ssl_cert`, `ssl_key` and `ssl_ca` are configured, and
the `[sst]` section has its own options as well. Encrypting replication and
leaving SST in clear text is a common and quiet failure, and it is the
worse half to get wrong, because SST is the transfer that carries
everything at once.

Using TLS with mutual certificate verification also upgrades "can reach the
port" into "is an authorised node", so it addresses the passive capture
problem and the rogue joiner problem with a single mechanism.

## Why OpenStack runs it this way

With the mechanics established, the shape of a typical OpenStack control
plane database deployment stops looking arbitrary.

**Three nodes, or three plus arbitrator.** Because quorum requires more
than half, and two is a trap. Note also the number people tend not to
question: it is three and not seven, because beyond the quorum requirement
each additional node costs write performance while contributing no write
capacity at all.

**Every write to a single node, via the load balancer.** This is the part
that surprises people, because Galera is proudly multi-master. The
certification mechanism makes multi-master *safe* -- it will never let the
cluster diverge -- but safe is not the same as pleasant. Writing to all
three nodes means genuinely concurrent transactions on different nodes,
which means certification failures, which surface to the application as
deadlock errors at commit time. Directing all writes to one node reduces
cross-node conflicts to approximately zero, because conflicting
transactions now meet each other in ordinary local InnoDB lock contention
instead, which is a well understood thing that databases have been good at
for decades. The standard HAProxy configuration therefore lists one node as
active and the others as backup:

```text title="haproxy.cfg (fragment)"
listen galera
    bind 10.0.0.10:3306
    mode tcp
    option httpchk
    balance source
    server db1 10.0.0.11:3306 check port 9200 inter 2s rise 2 fall 2
    server db2 10.0.0.12:3306 check port 9200 inter 2s rise 2 fall 2 backup
    server db3 10.0.0.13:3306 check port 9200 inter 2s rise 2 fall 2 backup
```

The health check on port 9200 is a small HTTP responder (`clustercheck`)
which reports the node's `wsrep_local_state`, so that HAProxy stops sending
traffic to a node that is donating, desynced, or non-Primary rather than
discovering the problem via query errors (1).
{ .annotate }

1. This is also the hook you use during a backup: the same check reports
   the desynced state, so a correctly configured HAProxy removes the node
   from the pool automatically when you set `wsrep_desync=ON`. Whether you
   want that automation or prefer explicit control is a matter of taste,
   but knowing which one you have is not optional.

**`SELECT ... FOR UPDATE` mostly removed from the code.** Recall from step
one that locks are local. In the multi-writer configuration OpenStack
originally hoped to use, `FOR UPDATE` provided no mutual exclusion between
nodes at all, while creating exactly the conflicting write sets that
certification then rejected as deadlocks. Nova and its siblings did
substantial work to remove the pattern in favour of compare-and-swap style
updates and explicit retry logic. `oslo.db` grew retry-on-deadlock
decorators for the general case, which is the correct application-level
response to step five.

**Read-your-writes handled explicitly.** Because API requests may hit a
different node than the one that served the preceding write, OpenStack
services either route reads to the same writer node or rely on
`wsrep_sync_wait` where causality genuinely matters.

None of these are workarounds for Galera being deficient. They are the
natural consequences of choosing a system that guarantees no divergence,
and paying for that guarantee in conflict handling rather than in
coordination latency.

!!! info "Want to know more?"

    | Topic | Resource |
    |-------|----------|
    | Galera documentation | [galeracluster.com/library](https://galeracluster.com/library/) is the provider's own documentation and is the authoritative reference for `wsrep_provider_options`. |
    | MariaDB Galera guide | [mariadb.com/kb/en/galera-cluster/](https://mariadb.com/kb/en/galera-cluster/) covers the MariaDB-specific integration, including SST configuration and encryption. |
    | Certification based replication | The original paper is Kemme and Alonso, "Don't be lazy, be consistent: Postgres-R, a new way to implement Database Replication" (VLDB 2000), which describes the technique Galera implements. |
    | State transfer internals | The `wsrep_sst_mariabackup` script shipped with MariaDB is readable shell, and is the fastest way to understand exactly what an SST does. |
    | OpenStack deployment guidance | The OpenStack HA guide documents the HAProxy-plus-single-writer pattern and the reasoning behind it. |

## Conclusion

Galera achieves something that sounds impossible at first hearing: multiple
writable database servers that can never diverge, without a distributed
lock manager and without a coordination round trip per statement. It does
this by inverting the usual approach. Rather than coordinating *before*
doing work, it does the work optimistically, then uses a total order
broadcast to convert conflict resolution into a deterministic local
computation that every node performs independently and identically.

Almost everything operators find surprising is the bill for that choice.
Deadlock errors at commit time exist because conflicts are detected after
the work is done rather than before. Flow control exists because apply is
asynchronous and something must prevent unbounded divergence, and it is
cluster-wide because the guarantee is cluster-wide. Desync exists as the
formal way to excuse a node from that contract, which is why it is the
foundation of both state transfers and backups. And the gcache is a ring
buffer rather than a log because its only job is to make brief absences
cheap, which is precisely why it must never be mistaken for a backup.

The same reasoning explains what Galera is not. Because every node applies
every write, nodes are copies rather than shards, and a cluster of them is
a redundancy mechanism rather than a scale-out one. That is a perfectly
good thing to be -- it is exactly what a cloud control plane database needs
-- but it means the cluster's write performance is bounded by a single
machine's ability to apply the workload, and always will be, whatever you
spend on additional nodes.

Once those connections are visible, the operational advice stops being a
list of rules. Size the gcache for your longest maintenance window. Watch
the receive queue on every node, not just the writer, and remember that
nothing will be logged when the cluster stalls. Desync before you back up,
and only one node at a time. Keep binary logs even though nothing demands
them. Put the replication network somewhere private and encrypt all three
ports. And when the cluster is slow, make the slowest node faster rather
than adding another one. Every one of those follows from the mechanism.

*[WSREP]: Write Set REPlication -- the API through which a database server hands transactions to a replication provider.
*[SST]: State Snapshot Transfer -- a full copy of the database sent to a joining node.
*[IST]: Incremental State Transfer -- replay of missed write sets from a donor's gcache.
*[TOI]: Total Order Isolation -- schema changes executed at an agreed point in the global sequence on every node.
*[RSU]: Rolling Schema Upgrade -- schema changes applied one desynced node at a time.
*[GTID]: Global Transaction ID -- in Galera, the cluster UUID plus a sequence number.
*[DDL]: Data Definition Language -- schema changing statements such as CREATE and ALTER.
*[WAN]: Wide Area Network
*[LDAP]: Lightweight Directory Access Protocol
*[DN]: Distinguished Name -- an entry's unique, hierarchical path in an LDAP directory.

--8<-- "docs-include/abbreviations.md"
