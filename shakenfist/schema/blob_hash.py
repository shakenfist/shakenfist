# Pydantic schema for blob hash storage in MariaDB.
#
# This schema defines the structure for storing blob checksums/hashes.
# The table tracks hash values computed by each node, enabling:
# - O(1) hash lookups via idx_hash_lookup index
# - Per-node verification tracking
# - Support for multiple hash algorithms (sha512, sha256, sha1, xxh128)
#
# The combination of (blob_uuid, node, algorithm) forms the primary key.
# This allows each node to independently verify and store hash values.

from typing import Annotated
from typing import Any
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class BlobHash(BaseModel):
    """Schema for blob hash storage in MariaDB.

    This model represents a single hash value for a blob on a specific node.
    The table enables queries like "find blob by hash" (O(1) via index) and
    "which blobs need re-verification" (via last_verified_at).

    The primary key is a compound of (blob_uuid, node, algorithm). This ensures:
    - Each node can independently verify blob integrity
    - Multiple hash algorithms can be stored per blob
    - Queries can efficiently find blobs by hash value

    Attributes:
        blob_uuid: The UUID of the blob this hash belongs to.
        node: The node FQDN where this hash was computed/verified.
        algorithm: The hash algorithm used (sha512, sha256, sha1, xxh128).
        hash_value: The computed hash value as a hex string.
        file_size: The size of the blob file in bytes when hash was computed.
        computed_at: Unix timestamp when the hash was first computed.
        last_verified_at: Unix timestamp when the hash was last verified.
        verification_status: Status of last verification (valid, invalid, pending).
        error_message: Error details if verification_status is 'invalid'.
    """

    model_config = ConfigDict(
        json_schema_extra={
            'sql_indexes': [
                # Status lookup: "Which hashes for this blob are valid?"
                ['blob_uuid', 'verification_status'],
                # Node lookup: "What hashes does this node have?"
                ['node'],
                # Stale lookup: "Which hashes need re-verification?"
                ['last_verified_at'],
                # Status queries: "Show all invalid hashes"
                ['verification_status'],
                # Hash lookup: "Find blob by hash" - THE KEY INDEX for O(1) lookup
                ['algorithm', 'hash_value'],
            ]
        }
    )

    # Primary key fields
    blob_uuid: Annotated[str, Field(max_length=36)]
    node: Annotated[str, Field(max_length=255)]
    algorithm: Annotated[str, Field(max_length=32)]

    # Hash data
    hash_value: Annotated[str, Field(max_length=256)]
    file_size: int

    # Timestamps
    computed_at: float  # Unix timestamp when hash was first computed
    last_verified_at: float  # Unix timestamp when hash was last verified

    # Verification status
    verification_status: Annotated[str, Field(max_length=16)] = 'pending'
    error_message: Optional[str] = None

    def external_view(self) -> dict[str, Any]:
        """Serialize BlobHash for JSON API responses.

        Returns:
            A dictionary suitable for JSON serialization containing all fields
            of the BlobHash.
        """
        return {
            'blob_uuid': self.blob_uuid,
            'node': self.node,
            'algorithm': self.algorithm,
            'hash_value': self.hash_value,
            'file_size': self.file_size,
            'computed_at': self.computed_at,
            'last_verified_at': self.last_verified_at,
            'verification_status': self.verification_status,
            'error_message': self.error_message
        }
