# Copyright 2019 Michael Still and contributors
# Pydantic schema for blob transfer storage in MariaDB.
#
# This schema defines the structure for storing blob transfer coordination
# data. The table replaces two etcd key patterns:
# - /sf/transfer/{source_node}/{transfer_name} - transfer handshake
# - /sf/attribute/blob/{uuid}/incomplete_locations - progress tracking
#
# The combination of (source_node, transfer_name) forms the primary key.
# Each transfer represents a single blob being copied from one node to another.

from typing import Annotated
from typing import Any
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class BlobTransfer(BaseModel):
    """Schema for blob transfer coordination in MariaDB.

    This model represents a single blob transfer operation between two nodes.
    The table enables queries like "what transfers are pending on this node"
    (for the transfers daemon) and "what transfers are in progress for this
    blob" (for replication decisions).

    The primary key is a compound of (source_node, transfer_name). This ensures:
    - Each transfer is uniquely identified
    - The transfers daemon can efficiently poll for pending work
    - Replication logic can find all in-progress transfers for a blob

    Attributes:
        source_node: The node FQDN that has the blob (server side).
        transfer_name: Unique identifier for this transfer (UUID-like).
        requesting_node: The node IP requesting the transfer (client side).
        blob_uuid: The UUID of the blob being transferred.
        token: Authentication token for the TCP connection.
        server_state: State of the transfer ('initial' or 'created').
        port: TCP port the server is listening on (None until created).
        percentage: Transfer progress from 0.0 to 100.0.
        created_at: Unix timestamp when the transfer was initiated.
        updated_at: Unix timestamp of last update (for stale cleanup).
    """

    model_config = ConfigDict(
        json_schema_extra={
            'sql_indexes': [
                # Source node lookup: "What transfers are pending on this node?"
                # Used by transfers daemon polling
                ['source_node'],
                # Blob lookup: "What transfers are in progress for this blob?"
                # Used by replication decisions to avoid over-replicating
                ['blob_uuid'],
                # Stale lookup: "Which transfers haven't been updated recently?"
                # Used for cleanup of abandoned transfers
                ['updated_at'],
            ]
        }
    )

    # Primary key fields
    source_node: Annotated[str, Field(max_length=255)]
    transfer_name: Annotated[str, Field(max_length=64)]

    # Transfer metadata
    requesting_node: Annotated[str, Field(max_length=255)]
    blob_uuid: Annotated[str, Field(max_length=36)]
    token: Annotated[str, Field(max_length=64)]

    # Server-side state
    server_state: Annotated[str, Field(max_length=16)]
    port: Optional[int] = None

    # Progress tracking (replaces incomplete_locations)
    percentage: float = 0.0

    # Timestamps
    created_at: float  # Unix timestamp when transfer was initiated
    updated_at: float  # Unix timestamp of last update

    def external_view(self) -> dict[str, Any]:
        """Serialize BlobTransfer for JSON API responses.

        Deliberately without the token. ``token`` authorises the inbound
        connection to the transfer server -- the transfers daemon
        compares it against what the client presents before sending a
        byte of blob data -- so it is a bearer credential rather than
        metadata about the transfer.

        Every caller of this method put the result somewhere a credential
        must not go: two audit events in blob.py, and the log fields in
        daemons/transfers/main.py. Events are written to MariaDB and also
        emitted to the log stream, which ships to Loki, so until this
        field was removed a live transfer token left the cluster on every
        blob transfer -- in exactly the ``extra={'token': token}`` shape
        phase 2's step 2g removed five times over in the authentication
        code. Found while sweeping for secret-carrying fields in phase 6.

        Nothing needed it here; the daemon reads ``.token`` from the model
        directly. This mirrors NamespaceKey.external_view(), which omits
        the hash and the nonce for the same reason.

        Returns:
            A dictionary suitable for JSON serialization containing the
            non-secret fields of the BlobTransfer.
        """
        return {
            'source_node': self.source_node,
            'transfer_name': self.transfer_name,
            'requesting_node': self.requesting_node,
            'blob_uuid': self.blob_uuid,
            'server_state': self.server_state,
            'port': self.port,
            'percentage': self.percentage,
            'created_at': self.created_at,
            'updated_at': self.updated_at
        }
