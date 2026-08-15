# Credential rotation

Shaken Fist holds a small number of long-lived secrets. This page
covers what they are, how to rotate each one, and what breaks while
you do.

It also records a disclosure: three of those secrets were written
into daemon logs in plaintext by releases before v0.8.0. If you are
upgrading an existing cluster, read
[Credentials disclosed before v0.8.0](#credentials-disclosed-before-v080)
first — installing the fix stops the disclosure continuing, but it
cannot undo the one that already happened.

## The cluster's long-lived secrets

| Secret | Where it lives | What holds it |
|--------|----------------|---------------|
| `AUTH_SECRET_SEED` | `cluster_config` in MariaDB, set from the `auth_secret` deployer variable | Signs every JWT the cluster issues. Anyone holding it can mint a token for any namespace. |
| `MARIADB_PASSWORD` | `/etc/sf/config` on database-tier nodes only, from the `mariadb_password` deployer variable | Direct MariaDB access. |
| `LOKI_AUTH_HEADER` | `/etc/sf/config` on **every** node, from the `loki_auth_header` deployer variable | Authenticates log pushes to your Loki. |
| `system_key` | `sfrc` on each node | The `system` namespace's authentication key. |
| `KERBSIDE_JWT_SIGNING_KEY` | `cluster_config` | Signs VDI console tokens. Rotation has its own procedure — see [VDI console tokens](vdi_console_tokens.md). |

Namespace key secrets are not in this table because the cluster does
not hold them: only a bcrypt hash is stored, and the secret itself is
shown once at creation and is not recoverable. Rotating one means
creating it again with a new value, which is what
`sf-client namespace add-key` does for an existing key name.

## Credentials disclosed before v0.8.0

Until v0.8.0, the `sf-queues` daemon logged **every** configuration
item at INFO on each start, as `Configuration item <NAME> = <value>`.
Secrets were not excluded. INFO and above is shipped off the node (see
[Logging](logging.md)), so on every daemon start these values left the
node in plaintext:

- **`AUTH_SECRET_SEED`** — on every node, since the seed reaches every
  node's environment from `cluster_config`. This is the most serious of
  the three: it signs every JWT in the zone, so holding it is
  equivalent to holding credentials for every namespace including
  `system`.
- **`MARIADB_PASSWORD`** — on database-tier nodes, which are the only
  ones it is rendered onto.
- **`LOKI_AUTH_HEADER`** — on every node, if you configured one. Note
  that this one was shipped *to the service it authenticates against*.

Separately, `BlobTransfer.external_view()` included the transfer's
authorisation token, and all three of its callers put the result into
audit events or log fields. A live transfer token therefore appeared
in the event log and the log stream on every blob transfer between
nodes.

Where those log lines ended up depends on your deployment:

- **Loki configured** — they are in your Loki, subject to its
  retention.
- **No Loki configured** — they are in each node's systemd journal,
  subject to journald's retention.
- **Releases predating the rsyslog removal** — the central forwarder
  shipped everything except DEBUG, so they are also in
  `/var/log/syslog` on the node that was then the primary.

The blob transfer tokens are additionally in the `events` table in
MariaDB, which log retention does not cover.

v0.8.0 fixes both causes: the configuration dumps redact by
configuration key name *and* the three options are typed so that
stringifying one yields asterisks, and `external_view()` no longer
carries the token. Neither change reaches backwards.

### Confirming exposure on your own cluster

If you ship to Loki, this returns the affected lines:

```logql
{job="shakenfist"} |= "Configuration item AUTH_SECRET_SEED"
{job="shakenfist"} |= "Configuration item MARIADB_PASSWORD"
{job="shakenfist"} |= "Configuration item LOKI_AUTH_HEADER"
```

Search a wide window — the banner is written once per daemon start, so
on a stable cluster the most recent hit may be old. **The results
contain the secrets in plaintext.** Run them somewhere you are willing
to have the value displayed, and do not paste the output into a
ticket.

Without Loki, the equivalent on each node is:

```bash
journalctl -u sf-queues | grep 'Configuration item AUTH_SECRET_SEED'
```

For the transfer tokens:

```logql
{job="shakenfist"} |= "transfer_name" |= "token"
```

### What to do about it

Treat all three as compromised to the extent that anyone who could
read your logs could read them. If your Loki and your journals are
reachable only by the same people who could already read
`/etc/sf/config`, the practical exposure is small and rotation is
housekeeping. If your log store is shared, has broader access than
your hosts, or is operated by someone else, rotate.

Rotate in this order, since the first one invalidates tokens the
others' procedures may be using:

1. `AUTH_SECRET_SEED`
2. `MARIADB_PASSWORD`
3. `LOKI_AUTH_HEADER`

Then consider purging the log store. Loki's delete API can remove
matching lines within a tenant's retention, but note that a delete
request is itself scoped by a LogQL selector, so you will be handing
Loki the same query as above. Purging the `events` rows carrying blob
transfer tokens is not necessary in the same way: a transfer token is
only useful while that transfer's server socket is listening, which is
seconds to minutes, and every one of them is long dead.

## Rotating `AUTH_SECRET_SEED`

**Blast radius: every outstanding token in the zone stops validating
immediately.** Every client re-authenticates on its next request,
which for `sf-client` and the Ansible collection happens
automatically. Long-running sessions holding a token will see a single
401 and recover. There is no overlap or grace period — the cluster
verifies with one seed, not a set.

The seed lives in `cluster_config`, so it is set once for the cluster
rather than per node:

```bash
# On a database-tier node. Passed on stdin so it never reaches the
# process table.
openssl rand -hex 32 | sf-ctl set-config --value-from-stdin AUTH_SECRET_SEED
```

`load_cluster_config()` reads `cluster_config` into the process
environment at import time, so **daemons continue using the old seed
until they restart**. Until every node has restarted, a token minted
by an already-restarted node will not validate on one that has not, so
do not treat this as a rolling change — restart the cluster's daemons
promptly, using the per-node sequence in
[Upgrades](upgrades.md#per-node-procedure):

```bash
sudo systemctl restart sf-api sf-cleaner sf-cluster sf-net sf-nodelock \
    sf-privexec sf-queues sf-resources sf-sidechannel sf-transfers
```

Update `auth_secret` in your `group_vars/all.yml` to match, or the
next deployer run will set it back.

## Rotating `MARIADB_PASSWORD`

This is your MariaDB's password, so the rotation is a MariaDB
operation with a Shaken Fist configuration change following it. Shaken
Fist reads the value from `/etc/sf/config` on database-tier nodes
only.

1. Change the password on the MariaDB server for the `mariadb_user`
   account (`shakenfist` by default).
2. Update `mariadb_password` in `group_vars/all.yml`.
3. Re-run the deployer to re-render `/etc/sf/config` on the
   database-tier nodes.
4. Restart `sf-database` on those nodes.

`sf-database` holds the only direct connections, so the window in
which the two disagree affects the database tier alone — but every
other daemon depends on that tier, so treat it as a cluster-wide
outage window rather than a rolling one.

## Rotating `LOKI_AUTH_HEADER`

The least disruptive of the three: log shipping is buffered through an
on-disk spool with retry, so a mismatch delays log delivery rather
than losing it, up to the spool's limits.

1. Issue the new credential in your Loki.
2. Update `loki_auth_header` in `group_vars/all.yml`.
3. Re-run the deployer. It is rendered on every node.
4. Restart the daemons on each node, as above.

Keep the old credential valid until every node has restarted, since
each node ships its own logs independently.

## See also

- [Logging](logging.md) — what ships off the node, and what stays on it
- [Authentication](authentication.md) — namespace keys, expiry and the
  event log
- [VDI console tokens](vdi_console_tokens.md) — rotating the Kerbside
  signing key
