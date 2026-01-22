# Database Schema

This document describes the Kerbside MySQL/MariaDB database schema.

## Entity Relationship Diagram

```
+------------------+          +------------------+
|     sources      |          |     consoles     |
+------------------+          +------------------+
| name (PK)        |---+      | uuid (PK)        |
| type             |   |      | source (FK)      |------+
| last_seen        |   +----->| discovered       |      |
| seen_by          |          | hypervisor       |      |
| errored          |          | hypervisor_ip    |      |
| url              |          | insecure_port    |      |
| ca_cert          |          | secure_port      |      |
| username         |          | name             |      |
| password         |          | host_subject     |      |
| project_name     |          | ticket           |      |
| user_domain_id   |          +------------------+      |
| project_domain_id|                 |                  |
| deleted          |                 |                  |
+------------------+                 |                  |
        |                            |                  |
        |    +------------------+    |                  |
        |    |  consoletokens   |    |                  |
        |    +------------------+    |                  |
        +--->| token (PK)       |<---+                  |
             | session_id       |                       |
             | uuid (FK)        |                       |
             | source (FK)      |                       |
             | created          |                       |
             | expires          |                       |
             +------------------+                       |
                    |                                   |
                    |                                   |
                    v                                   |
          +------------------+          +------------------+
          |  proxychannels   |          |   auditevents    |
          +------------------+          +------------------+
          | node (PK)        |          | source (PK)      |
          | pid (PK)         |          | uuid (PK)        |
          | created          |          | session_id       |
          | client_ip        |          | channel          |
          | client_port      |          | timestamp (PK)   |
          | connection_id    |          | node             |
          | channel_type     |          | pid              |
          | channel_id       |          | message          |
          | session_id (FK)  |          +------------------+
          +------------------+                  ^
                                                |
                                                +----------+
```

## Table Descriptions

### sources

Console sources (cloud platforms) that provide virtual machines.

| Column | Type | Description |
|--------|------|-------------|
| name | string | Primary key, source identifier |
| type | string | Source type: `shakenfist`, `ovirt`, or `openstack` |
| last_seen | datetime | Last successful poll time |
| seen_by | string | Node that last polled this source |
| errored | boolean | Whether the source is in error state |
| url | string | API URL for the source |
| ca_cert | text | CA certificate for TLS validation |
| username | string | Authentication username |
| password | string | Authentication password/API key |
| project_name | string | OpenStack project name (OpenStack only) |
| user_domain_id | string | OpenStack user domain (OpenStack only) |
| project_domain_id | string | OpenStack project domain (OpenStack only) |
| deleted | boolean | Soft delete flag |

### consoles

Virtual machine consoles discovered from sources.

| Column | Type | Description |
|--------|------|-------------|
| uuid | string | Primary key, VM UUID |
| source | string | Foreign key to sources.name |
| discovered | datetime | When the console was first discovered |
| hypervisor | string | Hypervisor hostname |
| hypervisor_ip | string | Hypervisor IP address |
| insecure_port | integer | Non-TLS SPICE port |
| secure_port | integer | TLS SPICE port |
| name | string | VM display name |
| host_subject | string | Expected TLS certificate subject |
| ticket | string | SPICE ticket for authentication |

### consoletokens

Time-limited access tokens for console connections.

| Column | Type | Description |
|--------|------|-------------|
| token | string | Primary key, 48-character access token |
| session_id | string | 12-character session identifier |
| uuid | string | Foreign key to consoles.uuid |
| source | string | Foreign key to sources.name |
| created | integer | Token creation time (epoch seconds) |
| expires | integer | Token expiration time (epoch seconds) |

### proxychannels

Active SPICE channel connections being proxied.

| Column | Type | Description |
|--------|------|-------------|
| node | string | Primary key part, proxy node hostname |
| pid | string | Primary key part, worker process ID |
| created | datetime | Connection establishment time |
| client_ip | string | Client IP address |
| client_port | integer | Client source port |
| connection_id | integer | SPICE connection ID |
| channel_type | string | Channel type name (main, display, etc.) |
| channel_id | integer | Channel instance ID |
| session_id | string | Foreign key to consoletokens.session_id |

### auditevents

Audit log for console access and protocol events.

| Column | Type | Description |
|--------|------|-------------|
| source | string | Primary key part, source name (no FK to avoid cascade) |
| uuid | string | Primary key part, console UUID (no FK to avoid cascade) |
| session_id | string | Session identifier (no FK to avoid cascade) |
| channel | string | Channel type or "session" for session events |
| timestamp | datetime | Event time with microsecond precision (PK part) |
| node | string | Proxy node that recorded the event |
| pid | string | Worker process ID |
| message | text | Event description |

## Relationships

- **sources → consoles**: One source provides many consoles
- **sources → consoletokens**: Tokens reference a source for validation
- **consoles → consoletokens**: Tokens grant access to a specific console
- **consoletokens → proxychannels**: Active channels reference their auth token
- **consoles → auditevents**: Audit events record console access (no FK to
  preserve audit history when consoles are deleted)

## Notes

- The `auditevents` table intentionally avoids foreign key constraints to
  preserve audit history even when sources or consoles are deleted.
- Timestamps in `consoletokens` use epoch seconds for easy expiration checks.
- The `proxychannels` table is used for connection tracking and cleanup.

## Related Documentation

- [Configuration](/components/kerbside/configuration/) - Database connection settings
- [Console Sources](/components/kerbside/console-sources/) - Source configuration
